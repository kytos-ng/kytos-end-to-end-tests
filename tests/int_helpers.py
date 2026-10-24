"""Shared helpers for the telemetry_int end-to-end suites.

Not a test module, following ``tests/p4ofswitch.py`` and ``tests/noviswitch.py``.

- ``tests/test_e2e_95_telemtry_int.py`` -- EVC shapes and convergence
- ``tests/test_e2e_96_telemetry_int.py`` -- telemetry_int REST API semantics
- ``tests/test_e2e_97_telemetry_int.py`` -- mef_eline-driven changes

Two constraints to know before changing anything here:

1. ``dpctl`` output is used only for ``cookie``, ``table``, ``priority`` and line
   counting. ``push_int`` / ``add_int_metadata`` / ``send_report`` / ``pop_int``
   are AmLight experimenter actions that ``ovs-ofctl`` cannot render, so every
   action-level assertion goes against flow_manager's stored flows.
2. ``check_consistency`` compares telemetry_int's expected flows against
   flow_manager's *stored* flows, never the switch. See
   ``delete_int_flows_stored`` versus ``delete_int_flows_on_switch``.
"""

import re
import time

import pytest
import requests


class INTHelper:
    """Mixin providing everything the telemetry_int e2e suites need.

    Switch-agnostic by design: test classes own their mininet switch objects and
    pass them in, keeping this a thin wrapper over the REST APIs plus the
    assertions built on them.
    """

    controller = "127.0.0.1"
    timeout = 10

    mef_cookie_prefix = 0xAA
    int_cookie_prefix = 0xA8
    int_owner = "telemetry_int"

    ipv4 = 2048
    tcp = 6
    udp = 17

    push_int = "push_int"
    add_int_metadata = "add_int_metadata"
    send_report = "send_report"
    pop_int = "pop_int"

    alien_vlan = 3999

    _cookie_re = re.compile(r"cookie=(0x[0-9a-fA-F]+)")
    _table_re = re.compile(r"table=(\d+)")
    _priority_re = re.compile(r"priority=(\d+)")

    @property
    def kytos_api(self):
        """Base URL of the kytos API."""
        return f"http://{self.controller}:8181/api"

    @property
    def mef_api(self):
        """Base URL of the mef_eline API."""
        return f"{self.kytos_api}/kytos/mef_eline/v2"

    @property
    def int_api(self):
        """Base URL of the telemetry_int API."""
        return f"{self.kytos_api}/kytos/telemetry_int/v1"

    @property
    def fm_api(self):
        """Base URL of the flow_manager API."""
        return f"{self.kytos_api}/kytos/flow_manager/v2"

    @property
    def topo_api(self):
        """Base URL of the topology API."""
        return f"{self.kytos_api}/kytos/topology/v3"

    @property
    def trace_api(self):
        """Base URL of the sdntrace_cp API."""
        return f"{self.kytos_api}/amlight/sdntrace_cp/v1"

    @staticmethod
    def parse_int_collector(data):
        """Parse raw file content from int_collector.py output and return a dict."""
        int_pkts = {}
        for pkt in data.splitlines():
            # format:
            # pkt_len=96 ip_src=1.0.0.1 ip_dst=1.0.0.3 udp_sport=10 udp_dport=80 int_stack=sw_id=0x1,...
            fields = pkt.split()
            if len(fields) == 6 and len(fields[5]) >= 10:
                int_pkts[f"{fields[2]} {fields[4]}"] = fields[5][10:].split(";")
        return int_pkts

    @staticmethod
    def error_description(response):
        """Return a kytos error response's ``description``, falling back to text."""
        try:
            return response.json().get("description", response.text)
        except ValueError:
            return response.text

    @staticmethod
    def evc_cookie(prefix, evc_id):
        """Build the full 64 bit cookie for an EVC id given a cookie prefix byte."""
        return (prefix << 56) | int(evc_id, 16)

    @staticmethod
    def switch_dpid(switch):
        """Return a mininet switch's dpid in the colon-separated kytos form."""
        raw = f"{int(switch.dpid, 16):016x}"
        return ":".join(raw[i : i + 2] for i in range(0, 16, 2))

    @staticmethod
    def path_dpids(path, start_dpid):
        """Order the switches of an EVC path, walking it from one UNI's switch.

        A kytos link orders its endpoints by sorted dpid, not by the direction
        traffic takes, so ``endpoint_a`` is not reliably the upstream end. This
        walks the chain instead, which is what an sdntrace_cp dpid sequence has
        to be compared against.
        """
        links = [
            (link[0].rsplit(":", 1)[0], link[1].rsplit(":", 1)[0]) for link in path
        ]
        sequence, current, remaining = [start_dpid], start_dpid, list(links)
        while remaining:
            for i, (end_a, end_z) in enumerate(remaining):
                if end_a == current:
                    current = end_z
                elif end_z == current:
                    current = end_a
                else:
                    continue
                sequence.append(current)
                remaining.pop(i)
                break
            else:
                raise AssertionError(
                    f"path {path} is not a chain starting at {start_dpid}; "
                    f"reached {current} with {remaining} unvisited"
                )
        return sequence

    @staticmethod
    def path_links(path):
        """Flatten an EVC path into ``[[endpoint_a_id, endpoint_b_id], ...]``."""
        return [[link["endpoint_a"]["id"], link["endpoint_b"]["id"]] for link in path]

    @staticmethod
    def apply_actions(stored_flow):
        """Return the ``apply_actions`` action list of a stored flow."""
        for instruction in stored_flow["flow"].get("instructions", []):
            if instruction["instruction_type"] == "apply_actions":
                return instruction["actions"]
        return []

    @staticmethod
    def action_types(stored_flow):
        """Return the ordered ``action_type`` list of a stored flow."""
        return [
            action["action_type"] for action in INTHelper.apply_actions(stored_flow)
        ]

    @staticmethod
    def goto_table(stored_flow):
        """Return the ``goto_table`` target of a stored flow, or None."""
        for instruction in stored_flow["flow"].get("instructions", []):
            if instruction["instruction_type"] == "goto_table":
                return instruction["table_id"]
        return None

    @staticmethod
    def build_trace(
        dpid, in_port, dl_vlan=None, dl_type=None, nw_proto=None, tp_dst=None
    ):
        """Build one sdntrace_cp trace entry."""
        entry = {"switch": {"dpid": dpid, "in_port": in_port}}
        eth = {}
        if dl_vlan is not None:
            eth["dl_vlan"] = dl_vlan
        if dl_type is not None:
            eth["dl_type"] = dl_type
        if eth:
            entry["eth"] = eth
        if nw_proto is not None:
            entry["ip"] = {"nw_proto": nw_proto}
        if tp_dst is not None:
            entry["tp"] = {"tp_dst": tp_dst}
        return {"trace": entry}

    @staticmethod
    def control_trace_entry(dpid, in_port, dl_vlan=None):
        """A trace entry that can never match an INT flow.

        Without ``dl_type``/``nw_proto`` the INT flows cannot match (their match
        fields have no counterpart in the request), so this exercises the plain
        mef_eline path and acts as the control for the INT trace.
        """
        return INTHelper.build_trace(dpid, in_port, dl_vlan=dl_vlan)

    @staticmethod
    def trace_dpids(steps):
        """Return the dpid sequence of a trace result."""
        return [step["dpid"] for step in steps]

    def mef_cookie(self, evc_id):
        """mef_eline cookie (0xaa prefix) for an EVC id."""
        return self.evc_cookie(self.mef_cookie_prefix, evc_id)

    def int_cookie(self, evc_id):
        """telemetry_int cookie (0xa8 prefix) for an EVC id."""
        return self.evc_cookie(self.int_cookie_prefix, evc_id)

    def _parse_flow_line(self, line):
        """Parse a single ``dpctl dump-flows`` line into a dict, or None."""
        match = self._cookie_re.search(line)
        if not match:
            return None
        table = self._table_re.search(line)
        priority = self._priority_re.search(line)
        actions = line.split("actions=", 1)
        return {
            "cookie": int(match.group(1), 16),
            "table": int(table.group(1)) if table else 0,
            "priority": int(priority.group(1)) if priority else 0,
            "actions": actions[1].strip() if len(actions) > 1 else "",
            "raw": line.strip(),
        }

    def dump_evc_flows(self, switch, evc_id):
        """Return ``{"mef": [...], "int": [...]}`` for an EVC on a given switch.

        Splitting by cookie prefix matters: a single ``cookie=0xa.<evc_id>`` regex
        conflates mef_eline (0xaa) with telemetry_int (0xa8), which would let a
        regression that drops every INT flow pass unnoticed.
        """
        wanted = {self.mef_cookie(evc_id): "mef", self.int_cookie(evc_id): "int"}
        result = {"mef": [], "int": []}
        for line in switch.dpctl("dump-flows").splitlines():
            flow = self._parse_flow_line(line)
            if flow is None:
                continue
            owner = wanted.get(flow["cookie"])
            if owner:
                result[owner].append(flow)
        return result

    def count_evc_flows(self, switch, evc_id):
        """Return ``(n_mef_flows, n_int_flows)`` for an EVC on a given switch."""
        flows = self.dump_evc_flows(switch, evc_id)
        return len(flows["mef"]), len(flows["int"])

    def count_evc_flows_by_table(self, switch, evc_id, owner="int"):
        """Return ``{table_id: count}`` for an EVC on a given switch."""
        counts = {}
        for flow in self.dump_evc_flows(switch, evc_id)[owner]:
            counts[flow["table"]] = counts.get(flow["table"], 0) + 1
        return counts

    def _flow_dump_report(self, switches, evc_id):
        """Human readable per-switch flow dump, for assertion messages."""
        lines = []
        for switch in switches:
            flows = self.dump_evc_flows(switch, evc_id)
            lines.append(
                f"  {switch.name}: {len(flows['mef'])} mef, {len(flows['int'])} int"
            )
            for flow in flows["mef"] + flows["int"]:
                lines.append(f"    {flow['raw']}")
        return "\n".join(lines)

    def assert_evc_flow_counts(self, switches, evc_id, expected, timeout=60):
        """Assert the per-switch (mef, int) flow counts for an EVC.

        ``switches`` is an iterable of mininet switch objects and ``expected`` maps
        a switch name to a ``(n_mef, n_int)`` tuple. Switches absent from
        ``expected`` are not checked. Polls until the counts match, since flow
        installation is asynchronous.
        """
        expected = {name: tuple(value) for name, value in expected.items()}
        targets = [sw for sw in switches if sw.name in expected]
        deadline = time.time() + timeout
        observed = {}
        while True:
            observed = {sw.name: self.count_evc_flows(sw, evc_id) for sw in targets}
            if observed == expected:
                return observed
            if time.time() >= deadline:
                break
            time.sleep(2)
        raise AssertionError(
            f"EVC {evc_id} flow counts mismatch.\n"
            f"  expected: {expected}\n  observed: {observed}\n"
            f"{self._flow_dump_report(targets, evc_id)}"
        )

    def assert_no_evc_flows(self, switches, evc_id, timeout=60):
        """Assert no mef_eline and no telemetry_int flows remain for an EVC."""
        self.assert_evc_flow_counts(
            switches, evc_id, {sw.name: (0, 0) for sw in switches}, timeout=timeout
        )

    def assert_no_int_flows(self, switches, evc_id, timeout=60):
        """Assert no telemetry_int flows remain, without constraining mef flows."""
        targets = list(switches)
        deadline = time.time() + timeout
        while True:
            observed = {sw.name: self.count_evc_flows(sw, evc_id)[1] for sw in targets}
            if not any(observed.values()):
                return observed
            if time.time() >= deadline:
                raise AssertionError(
                    f"EVC {evc_id} still has INT flows: {observed}\n"
                    f"{self._flow_dump_report(targets, evc_id)}"
                )
            time.sleep(2)

    def int_enable(self, evc_ids, force=None, proxy_port_enabled=None):
        """POST /v1/evc/enable. Returns the raw Response."""
        payload = {"evc_ids": list(evc_ids)}
        if force is not None:
            payload["force"] = force
        if proxy_port_enabled is not None:
            payload["proxy_port_enabled"] = proxy_port_enabled
        return requests.post(
            f"{self.int_api}/evc/enable", json=payload, timeout=self.timeout
        )

    def int_disable(self, evc_ids, force=None):
        """POST /v1/evc/disable. Returns the raw Response."""
        payload = {"evc_ids": list(evc_ids)}
        if force is not None:
            payload["force"] = force
        return requests.post(
            f"{self.int_api}/evc/disable", json=payload, timeout=self.timeout
        )

    def int_redeploy(self, evc_ids):
        """PATCH /v1/evc/redeploy. Returns the raw Response."""
        return requests.patch(
            f"{self.int_api}/evc/redeploy",
            json={"evc_ids": list(evc_ids)},
            timeout=self.timeout,
        )

    def int_list_evcs(
        self,
    ):
        """GET /v1/evc -- the INT-enabled EVCs, keyed by EVC id."""
        response = requests.get(f"{self.int_api}/evc", timeout=self.timeout)
        assert response.status_code == 200, response.text
        return response.json()

    def int_expected_flows_response(self, evc_ids):
        """POST /v1/evc/expected_flows. Returns the raw Response."""
        return requests.post(
            f"{self.int_api}/evc/expected_flows",
            json={"evc_ids": list(evc_ids)},
            timeout=self.timeout,
        )

    def int_expected_flows(self, evc_ids):
        """POST /v1/evc/expected_flows, asserting 200, returning the body."""
        response = self.int_expected_flows_response(evc_ids)
        assert response.status_code == 200, response.text
        return response.json()

    def int_check_consistency(self, evc_ids, outcome=None, inconsistent_action=None):
        """POST /v1/evc/check_consistency. Returns the raw Response."""
        params = {}
        if outcome is not None:
            params["outcome"] = outcome
        if inconsistent_action is not None:
            params["inconsistent_action"] = inconsistent_action
        return requests.post(
            f"{self.int_api}/evc/check_consistency",
            json={"evc_ids": list(evc_ids)},
            params=params,
            timeout=self.timeout,
        )

    def int_compare(
        self,
    ):
        """GET /v1/evc/compare -- EVCs whose INT flows disagree with their metadata."""
        response = requests.get(f"{self.int_api}/evc/compare", timeout=self.timeout)
        assert response.status_code == 200, response.text
        return response.json()

    def int_list_proxy_ports(
        self,
    ):
        """GET /v1/uni/proxy_port."""
        response = requests.get(f"{self.int_api}/uni/proxy_port", timeout=self.timeout)
        assert response.status_code == 200, response.text
        return response.json()

    def int_add_proxy_port(self, intf_id, port_no, force=None):
        """POST /v1/uni/{interface_id}/proxy_port/{port_number}."""
        params = {} if force is None else {"force": str(force).lower()}
        return requests.post(
            f"{self.int_api}/uni/{intf_id}/proxy_port/{port_no}",
            params=params,
            timeout=self.timeout,
        )

    def int_del_proxy_port(self, intf_id, force=None):
        """DELETE /v1/uni/{interface_id}/proxy_port."""
        params = {} if force is None else {"force": str(force).lower()}
        return requests.delete(
            f"{self.int_api}/uni/{intf_id}/proxy_port",
            params=params,
            timeout=self.timeout,
        )

    def mef_create_evc(self, payload):
        """POST /v2/evc/, asserting 201, returning the circuit_id."""
        response = requests.post(
            f"{self.mef_api}/evc/", json=payload, timeout=self.timeout
        )
        assert response.status_code == 201, response.text
        return response.json()["circuit_id"]

    def mef_patch_evc(self, evc_id, payload):
        """PATCH /v2/evc/{evc_id}."""
        return requests.patch(
            f"{self.mef_api}/evc/{evc_id}", json=payload, timeout=self.timeout
        )

    def mef_delete_evc(self, evc_id):
        """DELETE /v2/evc/{evc_id}."""
        return requests.delete(f"{self.mef_api}/evc/{evc_id}", timeout=self.timeout)

    def mef_redeploy_evc(self, evc_id, try_avoid_same_s_vlan=True):
        """PATCH /v2/evc/{evc_id}/redeploy."""
        params = {"try_avoid_same_s_vlan": str(try_avoid_same_s_vlan).lower()}
        return requests.patch(
            f"{self.mef_api}/evc/{evc_id}/redeploy", params=params, timeout=self.timeout
        )

    def mef_set_metadata(self, evc_id, metadata):
        """POST /v2/evc/{evc_id}/metadata."""
        return requests.post(
            f"{self.mef_api}/evc/{evc_id}/metadata", json=metadata, timeout=self.timeout
        )

    def mef_del_metadata_key(self, evc_id, key):
        """DELETE /v2/evc/{evc_id}/metadata/{key}."""
        return requests.delete(
            f"{self.mef_api}/evc/{evc_id}/metadata/{key}", timeout=self.timeout
        )

    def mef_list_evcs(self, archived=None):
        """GET /v2/evc/."""
        params = {} if archived is None else {"archived": str(archived).lower()}
        response = requests.get(
            f"{self.mef_api}/evc/", params=params, timeout=self.timeout
        )
        assert response.status_code == 200, response.text
        return response.json()

    def get_evc(self, evc_id):
        """GET /v2/evc/{evc_id}, asserting 200."""
        response = requests.get(f"{self.mef_api}/evc/{evc_id}", timeout=self.timeout)
        assert response.status_code == 200, response.text
        return response.json()

    def delete_all_evcs(self, wait=True):
        """Delete every non-archived EVC. Used by the light setup_method reset."""
        evcs = self.mef_list_evcs(archived=False)
        for evc_id in evcs:
            self.mef_delete_evc(evc_id)
        if wait and evcs:
            deadline = time.time() + 30
            while time.time() < deadline:
                if not self.mef_list_evcs(archived=False):
                    break
                time.sleep(1)
            else:
                raise AssertionError(
                    f"EVCs still present: {self.mef_list_evcs(archived=False)}"
                )
        return list(evcs)

    def get_evc_telemetry(self, evc_id):
        """Return the EVC's ``metadata.telemetry`` dict (``{}`` when absent)."""
        return self.get_evc(evc_id).get("metadata", {}).get("telemetry", {})

    def get_evc_proxy_port_enabled(self, evc_id):
        """Return the EVC's ``metadata.proxy_port_enabled`` value (None when absent)."""
        return self.get_evc(evc_id).get("metadata", {}).get("proxy_port_enabled")

    def assert_telemetry_metadata(
        self,
        evc_id,
        enabled,
        status=None,
        status_reason=None,
        timeout=60,
        prior=None,
    ):
        """Assert the EVC's ``metadata.telemetry``, polling until it matches.

        telemetry_int writes this metadata asynchronously (via mef_eline's metadata
        API), so a bare read right after a REST call races. Only the fields passed
        are checked; ``status_reason`` is compared as a sorted list.

        ``prior`` is the ``metadata.telemetry`` from before the operation under
        test (``{}`` when there was none), and opts into tolerating
        kytos-ng/mef_eline#731; see ``record_lost_write``.
        """
        expected = {"enabled": enabled}
        if status is not None:
            expected["status"] = status
        if status_reason is not None:
            expected["status_reason"] = sorted(status_reason)

        deadline = time.time() + timeout
        while True:
            observed = self.get_evc_telemetry(evc_id)
            actual = {"enabled": observed.get("enabled")}
            if status is not None:
                actual["status"] = observed.get("status")
            if status_reason is not None:
                actual["status_reason"] = sorted(observed.get("status_reason", []))
            if actual == expected:
                return observed
            if time.time() >= deadline:
                if prior is not None and self.record_lost_write(
                    evc_id, observed, prior
                ):
                    return observed
                raise AssertionError(
                    f"EVC {evc_id} telemetry metadata mismatch.\n"
                    f"  expected (subset): {expected}\n"
                    f"  observed: {actual}\n"
                    f"  full metadata: {self.get_evc(evc_id).get('metadata', {})}\n"
                    f"{self.dump_evc_state(evc_id)}"
                )
            time.sleep(2)

    def assert_telemetry_rewritten(self, evc_id, prior, timeout=60):
        """Assert telemetry_int wrote a fresh UP after a redeploy.

        A redeploy rewrites ``metadata.telemetry`` with the same UP status the
        EVC already had, so asserting UP alone passes even when the write never
        landed. ``status_updated_at`` is refreshed on every write, so it is
        what proves this one did. ``prior`` is ``get_evc_telemetry`` from just
        before the redeploy.

        A write lost to kytos-ng/mef_eline#731 leaves exactly ``prior``
        behind, which ``record_lost_write`` tolerates.
        """
        deadline = time.time() + timeout
        while True:
            observed = self.get_evc_telemetry(evc_id)
            if (
                observed.get("enabled") is True
                and observed.get("status") == "UP"
                and observed.get("status_reason") == []
                and observed.get("status_updated_at")
                != prior.get("status_updated_at")
            ):
                return observed
            if time.time() >= deadline:
                if self.record_lost_write(evc_id, observed, prior):
                    return observed
                raise AssertionError(
                    f"EVC {evc_id} telemetry was not rewritten UP.\n"
                    f"  prior: {prior}\n  observed: {observed}\n"
                    f"{self.dump_evc_state(evc_id)}"
                )
            time.sleep(2)

    def record_lost_write(self, evc_id, observed, prior):
        """Record a kytos-ng/mef_eline#731 lost write; return whether it was one.

        #731 reverts telemetry_int's bulk metadata write to the document it
        replaced, so its signature is exact: ``metadata.telemetry`` still
        equals ``prior`` -- ``status_updated_at`` included -- while the INT
        flows the same operation installed are in place, none pending.
        Anything else, a DOWN status or a missing ``telemetry`` with no INT
        flows for instance, is not #731 and the caller fails on it.

        The xfail itself is deferred to ``xfail_lost_writes`` so the flow and
        trace assertions after this one still run. Drop both, and every
        ``prior=``, once #731 is fixed.
        """
        if observed != prior:
            return False
        installed = self.stored_flow_ids(self.int_cookie_prefix, evc_id)
        pending = self.stored_flow_ids(
            self.int_cookie_prefix, evc_id, state="pending"
        )
        if not installed or pending:
            return False
        # pytest builds a fresh instance per test, so this never leaks
        self.__dict__.setdefault("_lost_writes", []).append(
            f"EVC {evc_id} telemetry metadata still {prior!r} with "
            f"{len(installed)} INT flows installed"
        )
        return True

    def lost_write_recorded(self):
        """Whether ``record_lost_write`` caught a lost write in this test.

        For guarding the assertions that read the metadata the lost write
        would have set, which can't pass once it's gone.
        """
        return bool(self.__dict__.get("_lost_writes"))

    def xfail_lost_writes(self):
        """``xfail`` if ``record_lost_write`` caught kytos-ng/mef_eline#731.

        Call it last in every test that passes ``prior``, so it only fires
        once everything else has passed.
        """
        if self.lost_write_recorded():
            pytest.xfail(
                "kytos-ng/mef_eline#731: bulk metadata write was reverted: "
                + "; ".join(self.__dict__["_lost_writes"])
            )

    def assert_proxy_port_enabled(self, evc_id, expected, timeout=30):
        """Assert the EVC's ``metadata.proxy_port_enabled``, polling until it matches.

        This is a sibling of ``metadata.telemetry``, not a field inside it, which
        is why it is asserted separately. ``None`` is a real value here -- it is
        what ``enable`` writes when the caller does not pass the flag.
        """
        deadline = time.time() + timeout
        while True:
            observed = self.get_evc_proxy_port_enabled(evc_id)
            if observed == expected:
                return observed
            if time.time() >= deadline:
                raise AssertionError(
                    f"EVC {evc_id} proxy_port_enabled is {observed!r}, "
                    f"expected {expected!r}. "
                    f"Full metadata: {self.get_evc(evc_id).get('metadata', {})}"
                )
            time.sleep(2)

    def get_stored_flows(self, cookie_prefix, evc_id, state="installed"):
        """Return ``{dpid: [stored_flow, ...]}`` for an EVC's cookie prefix.

        ``state`` may be a single state or an iterable of states.
        """
        cookie = self.evc_cookie(cookie_prefix, evc_id)
        states = [state] if isinstance(state, str) else list(state)
        params = [("cookie_range", cookie), ("cookie_range", cookie)]
        params += [("state", value) for value in states]
        response = requests.get(
            f"{self.fm_api}/stored_flows/", params=params, timeout=self.timeout
        )
        assert response.status_code == 200, response.text
        return {dpid: flows for dpid, flows in response.json().items() if flows}

    def stored_flow_ids(self, cookie_prefix, evc_id, state="installed"):
        """Return the set of ``flow_id`` for an EVC's cookie prefix."""
        stored = self.get_stored_flows(cookie_prefix, evc_id, state=state)
        return {flow["flow_id"] for flows in stored.values() for flow in flows}

    def assert_int_flow_actions(self, evc_id, uni_dpids, hop_dpids=()):
        """Assert the INT flow structure telemetry_int builds without proxy ports.

        Each UNI switch is both source (traffic from the UNI) and sink (traffic
        from an NNI), so every UNI dpid carries all four action patterns.

        Per UNI dpid:
          - table 0 ``push_int`` flows matching dl_type/nw_proto, with goto_table
          - a table X flow whose first action is ``add_int_metadata``
          - table 0 ``send_report`` flows matching dl_type/nw_proto, with goto_table
          - a table X flow whose first action is ``pop_int``
          - no flow mixes ``add_int_metadata`` with ``send_report``: the no-proxy-port
            sink deliberately does not add metadata

        Per hop dpid:
          - table 0 flows whose first action is ``add_int_metadata``
          - no ``push_int`` / ``send_report`` / ``pop_int`` anywhere

        Globally: owner, cookie prefix, and priority -- which differs by table.
        ``utils.set_priority`` is applied only to the table 0 copies, so those
        are mef_eline priority + 100 while the table X flow keeps the mef_eline
        priority unchanged.

        Every count is exact and derived from the mef_eline flows rather than
        from ``expected_flows``, so a FlowBuilder regression that drops or
        duplicates flows can't pass by changing both sides. Each mef_eline
        flow on a UNI switch becomes three INT flows: from the UNI port, a
        TCP and a UDP ``push_int`` plus one table X ``add_int_metadata``;
        from an NNI, a TCP and a UDP ``send_report`` plus one table X
        ``pop_int``. On a hop each one becomes a TCP and a UDP
        ``add_int_metadata``.
        """
        int_stored = self.get_stored_flows(self.int_cookie_prefix, evc_id)
        mef_stored = self.get_stored_flows(self.mef_cookie_prefix, evc_id)
        assert int_stored, f"EVC {evc_id} has no stored INT flows"
        evc = self.get_evc(evc_id)
        uni_ports = {}
        for uni in (evc["uni_a"], evc["uni_z"]):
            dpid, port = uni["interface_id"].rsplit(":", 1)
            uni_ports.setdefault(dpid, set()).add(int(port))

        expected_cookie = self.int_cookie(evc_id)
        mef_priorities = {
            flow["flow"]["priority"] for flows in mef_stored.values() for flow in flows
        }

        for dpid, flows in int_stored.items():
            for flow in flows:
                assert flow["flow"]["owner"] == self.int_owner, flow
                assert flow["flow"]["cookie"] == expected_cookie, flow
                priority = flow["flow"]["priority"]
                table_id = flow["flow"]["table_id"]
                if table_id == 0:
                    assert priority - 100 in mef_priorities, (
                        f"{dpid} table 0 INT flow priority {priority} is not a "
                        f"mef_eline priority + 100 ({sorted(mef_priorities)})"
                    )
                else:
                    assert priority in mef_priorities, (
                        f"{dpid} table {table_id} INT flow priority {priority} "
                        f"is not a mef_eline priority ({sorted(mef_priorities)}); "
                        "table X flows keep it unchanged"
                    )
                types = self.action_types(flow)
                assert not (
                    self.add_int_metadata in types and self.send_report in types
                ), (
                    f"{dpid} flow mixes add_int_metadata with send_report, but the "
                    f"no-proxy-port sink must not add metadata: {flow}"
                )

        for dpid in uni_dpids:
            flows = int_stored.get(dpid)
            assert flows, f"EVC {evc_id} has no INT flows on UNI switch {dpid}"
            mef_flows = mef_stored.get(dpid, [])
            n_from_uni = sum(
                1
                for flow in mef_flows
                if flow["flow"]["match"].get("in_port") in uni_ports.get(dpid, ())
            )
            n_from_nni = len(mef_flows) - n_from_uni
            assert n_from_uni, f"{dpid} has no mef_eline flow from a UNI port"
            assert len(flows) == 3 * len(mef_flows), (
                f"{dpid} has {len(flows)} INT flows for {len(mef_flows)} "
                f"mef_eline flows, expected {3 * len(mef_flows)}"
            )

            push = [f for f in flows if self.action_types(f) == [self.push_int]]
            assert len(push) == 2 * n_from_uni, (
                f"{dpid} expected {2 * n_from_uni} push_int flows, got {len(push)}"
            )
            for flow in push:
                assert flow["flow"]["table_id"] == 0, flow
                assert flow["flow"]["match"].get("dl_type") == self.ipv4, flow
                assert flow["flow"]["match"].get("nw_proto") in (
                    self.tcp,
                    self.udp,
                ), flow
                assert self.goto_table(flow) is not None, flow
            assert {f["flow"]["match"]["nw_proto"] for f in push} == {
                self.tcp,
                self.udp,
            }, push

            report = [f for f in flows if self.action_types(f) == [self.send_report]]
            assert len(report) == 2 * n_from_nni, (
                f"{dpid} expected {2 * n_from_nni} send_report flows, "
                f"got {len(report)}"
            )
            for flow in report:
                assert flow["flow"]["table_id"] == 0, flow
                assert flow["flow"]["match"].get("dl_type") == self.ipv4, flow
                assert flow["flow"]["match"].get("nw_proto") in (
                    self.tcp,
                    self.udp,
                ), flow
                assert self.goto_table(flow) is not None, flow
            assert {f["flow"]["match"]["nw_proto"] for f in report} == {
                self.tcp,
                self.udp,
            }, report

            metadata_first = [
                f
                for f in flows
                if self.action_types(f)[:1] == [self.add_int_metadata]
                and f["flow"]["table_id"] != 0
            ]
            assert len(metadata_first) == n_from_uni, (
                f"{dpid} expected {n_from_uni} table X add_int_metadata flows, "
                f"got {len(metadata_first)}"
            )

            pop_first = [f for f in flows if self.action_types(f)[:1] == [self.pop_int]]
            assert len(pop_first) == n_from_nni, (
                f"{dpid} expected {n_from_nni} pop_int flows, got {len(pop_first)}"
            )
            for flow in pop_first:
                assert flow["flow"]["table_id"] != 0, flow

        for dpid in hop_dpids:
            flows = int_stored.get(dpid)
            assert flows, f"EVC {evc_id} has no INT flows on hop switch {dpid}"
            n_mef = len(mef_stored.get(dpid, []))
            assert len(flows) == 2 * n_mef, (
                f"hop {dpid} has {len(flows)} INT flows for {n_mef} mef_eline "
                f"flows, expected {2 * n_mef}"
            )
            for flow in flows:
                assert (
                    flow["flow"]["table_id"] == 0
                ), f"hop flows are table 0 only: {flow}"
                types = self.action_types(flow)
                assert types[:1] == [self.add_int_metadata], flow
                assert (
                    self.push_int not in types and self.send_report not in types
                ), flow
                assert self.pop_int not in types, flow
            assert {f["flow"]["match"]["nw_proto"] for f in flows} == {
                self.tcp,
                self.udp,
            }, flows

    def assert_int_consistent(self, evc_id, timeout=45):
        """Assert telemetry_int considers the EVC's INT flows consistent.

        Stronger than any hardcoded flow count: it diffs telemetry_int's own
        ``expected_flows`` against flow_manager's stored INT flows, so it works for
        every EVC shape with no per-shape arithmetic.
        """
        deadline = time.time() + timeout
        last = None
        while True:
            response = self.int_check_consistency([evc_id], outcome="inconsistent")
            if response.status_code == 200:
                last = response.json()
                if not last:
                    break
            else:
                last = response.text
            if time.time() >= deadline:
                raise AssertionError(f"EVC {evc_id} INT flows are inconsistent: {last}")
            time.sleep(2)

        response = self.int_check_consistency([evc_id], outcome="consistent")
        assert response.status_code == 200, response.text
        data = response.json()
        assert evc_id in data, f"EVC {evc_id} missing from consistent outcome: {data}"
        result = data[evc_id]
        assert result["outcome"] == "consistent", result
        assert result["missing_flows"] == [], result
        assert result["alien_flows"] == [], result
        return result

    def assert_int_flows_match_expected(self, evc_id, switches, timeout=60):
        """Cross-check the switches against telemetry_int's own ``expected_flows``.

        Asserts the total, the per-table split and the per-switch split all agree
        between ``POST /v1/evc/expected_flows`` and ``dpctl dump-flows``.
        """
        deadline = time.time() + timeout
        while True:
            expected = self.int_expected_flows([evc_id])
            assert (
                evc_id in expected
            ), f"EVC {evc_id} missing from expected_flows: {expected}"
            data = expected[evc_id]

            assert data["count_total"] == len(data["flows"]), data
            by_table = {int(key): value for key, value in data["count_table"].items()}
            assert sum(by_table.values()) == data["count_total"], data

            by_switch = {}
            for flow in data["flows"]:
                by_switch[flow["switch"]] = by_switch.get(flow["switch"], 0) + 1

            observed_total = 0
            observed_by_table = {}
            observed_by_switch = {}
            for switch in switches:
                count = self.count_evc_flows(switch, evc_id)[1]
                observed_total += count
                if count:
                    observed_by_switch[self.switch_dpid(switch)] = count
                for table, value in self.count_evc_flows_by_table(
                    switch, evc_id
                ).items():
                    observed_by_table[table] = observed_by_table.get(table, 0) + value
            matched = (
                observed_total == data["count_total"]
                and observed_by_table == by_table
                and observed_by_switch == by_switch
            )
            if matched:
                return data
            if time.time() >= deadline:
                raise AssertionError(
                    f"EVC {evc_id} installed INT flows disagree with expected_flows.\n"
                    f"  expected total={data['count_total']} by_table={by_table} "
                    f"by_switch={by_switch}\n"
                    f"  observed total={observed_total} by_table={observed_by_table} "
                    f"by_switch={observed_by_switch}\n"
                    f"{self._flow_dump_report(switches, evc_id)}"
                )
            time.sleep(2)

    def trace(self, dpid, in_port, **kwargs):
        """PUT /v1/trace -- returns the list of trace steps."""
        response = requests.put(
            f"{self.trace_api}/trace",
            json=self.build_trace(dpid, in_port, **kwargs),
            timeout=self.timeout,
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    def traces(self, entries):
        """PUT /v1/traces -- returns a list of trace step lists."""
        response = requests.put(
            f"{self.trace_api}/traces", json=entries, timeout=self.timeout
        )
        assert response.status_code == 200, response.text
        return response.json()["result"]

    def int_trace_entry(self, dpid, in_port, dl_vlan=None, nw_proto=None, tp_dst=80):
        """A trace entry that can only match telemetry_int flows.

        The INT table-0 flows match ``dl_type=2048`` plus ``nw_proto`` 6/17, and
        sdntrace_cp's ``do_match`` compares each stored-flow match field against
        the request, so a trace carrying those fields selects the INT flow (higher
        priority) and then follows its ``goto_table`` into the INT table X flow.

        ``nw_proto`` defaults to TCP.
        """
        return self.build_trace(
            dpid,
            in_port,
            dl_vlan=dl_vlan,
            dl_type=self.ipv4,
            nw_proto=self.tcp if nw_proto is None else nw_proto,
            tp_dst=tp_dst,
        )

    def assert_int_trace_matches_control(
        self, dpid, in_port, dl_vlan, expected_dpids, timeout=30
    ):
        """Assert the INT path and the plain mef_eline path are both intact.

        Runs both traces in a single ``/v1/traces`` call; both must traverse
        ``expected_dpids``. This catches a table X flow that is missing or on a
        table the ``goto_table`` chain cannot reach: the table 0 INT flow still
        matches and jumps, nothing matches at the far end, and the INT trace
        stops early while the control trace does not.
        """
        expected = list(expected_dpids)
        deadline = time.time() + timeout
        while True:
            control, int_result = self.traces(
                [
                    self.control_trace_entry(dpid, in_port, dl_vlan=dl_vlan),
                    self.int_trace_entry(dpid, in_port, dl_vlan=dl_vlan),
                ]
            )
            got_control = self.trace_dpids(control)
            got_int = self.trace_dpids(int_result)
            if got_control == expected and got_int == expected:
                return control, int_result
            if time.time() >= deadline:
                raise AssertionError(
                    f"trace path mismatch, expected {expected}:\n"
                    f"  control trace: {got_control} ({control})\n"
                    f"  INT trace:     {got_int} ({int_result})"
                )
            time.sleep(2)

    def assert_no_int_trace(self, dpid, in_port, dl_vlan, expected_dpids, timeout=30):
        """Assert the INT trace no longer reaches the far end of the path.

        Polls, since stored_flows can lag the switch by a moment after a delete.
        """
        expected = list(expected_dpids)
        deadline = time.time() + timeout
        while True:
            steps = self.trace(
                dpid,
                in_port,
                dl_vlan=dl_vlan,
                dl_type=self.ipv4,
                nw_proto=self.tcp,
                tp_dst=80,
            )
            if self.trace_dpids(steps) != expected:
                return steps
            if time.time() >= deadline:
                raise AssertionError(
                    f"INT trace unexpectedly still traverses {expected}: {steps}"
                )
            time.sleep(2)

    def delete_int_flows_stored(self, dpid, evc_id, table_id=255):
        """Delete an EVC's INT flows through flow_manager.

        This removes them from ``stored_flows`` *and* from the switch, which is what
        telemetry_int's ``check_consistency`` detects as ``missing_flows`` (it diffs
        expected against stored, never against the switch). ``table_id`` 255
        means every table.
        """
        payload = {
            "force": True,
            "flows": [
                {
                    "cookie": self.int_cookie(evc_id),
                    "cookie_mask": 0xFFFFFFFFFFFFFFFF,
                    "table_id": table_id,
                    "owner": self.int_owner,
                }
            ],
        }
        response = requests.delete(
            f"{self.fm_api}/flows/{dpid}", json=payload, timeout=self.timeout
        )
        assert response.status_code == 202, response.text
        return response

    def delete_int_flows_on_switch(self, switch, evc_id):
        """Delete an EVC's INT flows directly on the switch, behind flow_manager.

        ``stored_flows`` stays intact, so telemetry_int keeps reporting the EVC as
        consistent; flow_manager's own consistency routine is what restores them.
        """
        cookie = self.int_cookie(evc_id)
        # no table= match: ovs-ofctl deletes from every table by default on OF1.1+,
        # which is what we want since the INT flows span table 0 and table X
        return switch.dpctl("del-flows", f"cookie={hex(cookie)}/-1")

    def install_alien_int_flow(
        self, dpid, evc_id, in_port=1, dl_vlan=None, priority=30000, out_port=3
    ):
        """Install an INT-cookie flow telemetry_int would never build.

        It lands in ``stored_flows`` but not in ``expected_flows``, which is what
        ``check_consistency`` reports as an ``alien_flows`` entry.

        The match deliberately uses a real ``in_port`` with an unused VLAN, and
        outputs to a real port, so the flow is accepted by a real pipeline (a match
        on a nonexistent port, or an empty action list, may be rejected) while
        being impossible to confuse with a real INT flow. Port 3 is the INT report
        port on both s1 and s6 in ``amlight_intlab``.
        """
        dl_vlan = self.alien_vlan if dl_vlan is None else dl_vlan
        actions = (
            [] if out_port is None else [{"action_type": "output", "port": out_port}]
        )
        payload = {
            "force": True,
            "flows": [
                {
                    "cookie": self.int_cookie(evc_id),
                    "owner": self.int_owner,
                    "table_id": 0,
                    "table_group": "base",
                    "priority": priority,
                    "match": {"in_port": in_port, "dl_vlan": dl_vlan},
                    "actions": actions,
                }
            ],
        }
        response = requests.post(
            f"{self.fm_api}/flows/{dpid}", json=payload, timeout=self.timeout
        )
        assert response.status_code == 202, response.text
        return response

    def get_interface(self, intf_id):
        """Return one interface's dict from ``GET /v3/interfaces``.

        topology has no single-interface GET (only ``/metadata`` and
        ``/tag_ranges`` sub-routes), so this reads the list, which is keyed by
        interface id under ``"interfaces"``.
        """
        response = requests.get(f"{self.topo_api}/interfaces", timeout=self.timeout)
        assert response.status_code == 200, response.text
        interfaces = response.json()["interfaces"]
        assert intf_id in interfaces, f"interface {intf_id} not in topology"
        return interfaces[intf_id]

    def dump_evc_state(self, evc_id):
        """Return a one-line summary of everything that decides EVC liveness.

        Every wait in this module is really waiting on one of these fields, so
        a timeout that prints them is the difference between a diagnosis and a
        guess.
        """
        try:
            evc = self.get_evc(evc_id)
        except AssertionError as exc:
            return f"evc {evc_id} unreadable: {exc}"
        mef = self.get_stored_flows(
            self.mef_cookie_prefix, evc_id, state=("installed", "pending")
        )
        int_ = self.get_stored_flows(
            self.int_cookie_prefix, evc_id, state=("installed", "pending")
        )
        return (
            f"evc {evc_id} name={evc.get('name')!r} "
            f"enabled={evc.get('enabled')} active={evc.get('active')} "
            f"archived={evc.get('archived')} "
            f"dynamic_backup_path={evc.get('dynamic_backup_path')}\n"
            f"  current={self.path_links(evc.get('current_path', []))}\n"
            f"  failover={self.path_links(evc.get('failover_path', []))}\n"
            f"  primary={self.path_links(evc.get('primary_path', []))}\n"
            f"  backup={self.path_links(evc.get('backup_path', []))}\n"
            f"  telemetry={evc.get('metadata', {}).get('telemetry')}\n"
            f"  stored mef={ {d: len(f) for d, f in mef.items()} } "
            f"int={ {d: len(f) for d, f in int_.items()} }"
        )

    def dump_nni_state(self, net=None):
        """Return the kytos-side state of every real inter-switch link.

        Covers the failure mode where a link is physically up in mininet but
        kytos has it DOWN, or of_lldp has disabled one of its interfaces --
        either leaves mef_eline unable to compute a path, which is what an
        empty ``current_path`` looks like from the outside.

        ``net`` defaults to the test class's own ``self.net``.
        """
        net = net or getattr(self, "net", None)
        if net is None:
            return "NNI links: unavailable (no NetworkTest on self)"
        response = requests.get(f"{self.topo_api}/links/", timeout=self.timeout)
        if response.status_code != 200:
            return f"links unreadable: {response.text}"
        links = response.json()["links"]
        lines = []
        for link in net.net.links:
            node_a, node_z = link.intf1.node, link.intf2.node
            if node_a not in net.net.switches or node_z not in net.net.switches:
                continue
            if node_a is node_z:
                continue
            link_id = net.create_link_id(link)
            data = links.get(link_id, {})
            endpoints = []
            for key in ("endpoint_a", "endpoint_z"):
                intf = data.get(key, {})
                endpoints.append(
                    f"{intf.get('id')}(enabled={intf.get('enabled')},"
                    f"active={intf.get('active')})"
                )
            lines.append(
                f"  {node_a.name}--{node_z.name} status={data.get('status')} "
                f"reason={data.get('status_reason')} "
                f"enabled={data.get('enabled')} active={data.get('active')} "
                + " ".join(endpoints)
            )
        return "NNI links:\n" + "\n".join(lines)

    def wait_nni_links_up(self, net, timeout=90):
        """Wait until every inter-switch link is UP, ignoring the loops.

        ``wait_kytos_links(status="UP")`` cannot be used on amlight_intlab --
        of_lldp disables the five loop interfaces, so those links stay DOWN --
        and the presence-only ``wait_kytos_links()`` returns while kytos still
        reports links DOWN, after which the next EVC deploys on the wrong path.
        """
        expected = {}
        for link in net.net.links:
            node_a, node_z = link.intf1.node, link.intf2.node
            if node_a not in net.net.switches or node_z not in net.net.switches:
                continue
            if node_a is node_z:
                continue
            link_id = net.create_link_id(link)
            if link_id:
                expected[link_id] = f"{node_a.name}--{node_z.name}"

        deadline = time.time() + timeout
        while True:
            response = requests.get(f"{self.topo_api}/links/", timeout=self.timeout)
            assert response.status_code == 200, response.text
            links = response.json()["links"]
            not_up = {
                name: links.get(link_id, {}).get("status", "MISSING")
                for link_id, name in expected.items()
                if links.get(link_id, {}).get("status") != "UP"
            }
            if not not_up:
                return expected
            if time.time() >= deadline:
                raise AssertionError(
                    f"NNI links never came UP: {not_up}\n"
                    f"{self.dump_nni_state(net)}"
                )
            time.sleep(2)

    def wait_interface_active(self, intf_id, active=True, timeout=60):
        """Wait until the topology reports an interface as (in)active."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.get_interface(intf_id)
            if last.get("active") is active:
                return last
            time.sleep(2)
        raise AssertionError(
            f"interface {intf_id} active != {active}: {last and last.get('active')}"
        )

    def wait_interface_looped(self, intf_id, timeout=60):
        """Wait until of_lldp has written the ``looped`` metadata on an interface.

        of_lldp writes ``looped.port_numbers`` before acting on ``LLDP_LOOP_ACTIONS``,
        so a ProxyPort's destination resolves even when the loop gets disabled.
        """
        deadline = time.time() + timeout
        interface = None
        while time.time() < deadline:
            interface = self.get_interface(intf_id)
            looped = interface.get("metadata", {}).get("looped")
            if looped and looped.get("port_numbers"):
                return looped
            time.sleep(2)
        raise AssertionError(
            f"Interface {intf_id} never got looped metadata.\n"
            f"  interface={interface}"
        )

    def require_looped_interface(self, intf_id, timeout=60):
        """Skip the test unless of_lldp has detected the loop on ``intf_id``.

        Loop detection is what makes a proxy port addressable at all, and it is
        a property of the backend rather than of telemetry_int: on P4OfSwitch
        of_lldp does not report the amlight_intlab loops, so the proxy-port API
        has nothing to point at. A skip keeps that distinction visible instead
        of reporting it as a telemetry_int regression.
        """
        try:
            return self.wait_interface_looped(intf_id, timeout=timeout)
        except AssertionError as exc:
            pytest.skip(
                f"of_lldp reported no loop on {intf_id} on this backend, so "
                f"there is no proxy port to exercise: {exc}"
            )

    def int_ready(self, evc_id):
        """Return whether the EVC's INT flows are consistent and none is pending.

        ``check_consistency`` counts ``pending`` stored flows as present, so a
        consistent EVC can still have flow mods in flight; it's only ready
        once none of its INT flows is pending anymore.

        Only a 503 is retried: any other error, such as the 500 on an
        intra-switch EVC without proxy ports (test_305), fails right away
        instead of polling until the caller's timeout.
        """
        response = self.int_check_consistency([evc_id], outcome="inconsistent")
        if response.status_code == 503:
            return False
        assert response.status_code == 200, (
            f"check_consistency on EVC {evc_id}: {response.status_code} "
            f"{response.text}"
        )
        if response.json():
            return False
        installed = self.stored_flow_ids(self.int_cookie_prefix, evc_id)
        pending = self.stored_flow_ids(
            self.int_cookie_prefix, evc_id, state="pending"
        )
        return bool(installed) and not pending

    def wait_int_ready(self, evc_id, timeout=60):
        """Wait until the EVC's INT flows are consistent and none is pending.

        ``POST /v1/evc/enable`` and ``status: UP`` both precede the flow mods
        landing, and they're sent per switch, so seeing some INT flows says
        nothing about the rest.
        """
        deadline = time.time() + timeout
        while not self.int_ready(evc_id):
            if time.time() >= deadline:
                response = self.int_check_consistency([evc_id])
                raise AssertionError(
                    f"EVC {evc_id} INT flows not ready after {timeout}s: "
                    f"{response.status_code} {response.text}\n"
                    f"{self.dump_evc_state(evc_id)}"
                )
            time.sleep(2)

    def wait_evc_active(self, evc_id, active=True, timeout=90, require_flows=None):
        """Wait until an EVC reaches the given ``active`` state.

        For ``active=True`` this also waits for the EVC's mef_eline flows to be
        in flow_manager's stored_flows: telemetry_int builds its flows from
        those and raises ``FlowsNotFound`` (a 404 on enable) if they are not
        there yet. ``require_flows=False`` drops that extra wait for callers
        that only care about the EVC's own state.
        """
        if require_flows is None:
            require_flows = active
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.get_evc(evc_id)
            if last.get("active") is active:
                if not require_flows or self.get_stored_flows(
                    self.mef_cookie_prefix, evc_id, state=("installed", "pending")
                ):
                    return last
            time.sleep(2)
        raise AssertionError(
            f"EVC {evc_id} never reached active={active} "
            f"(require_flows={require_flows}).\n{self.dump_evc_state(evc_id)}\n"
            f"{self.dump_nni_state()}"
        )

    def wait_evc_paths(self, evc_id, current=None, failover=None, timeout=60):
        """Wait until an EVC's current and/or failover path match exactly."""
        deadline = time.time() + timeout
        last_current = last_failover = None
        while True:
            evc = self.get_evc(evc_id)
            last_current = self.path_links(evc.get("current_path", []))
            last_failover = self.path_links(evc.get("failover_path", []))
            checks = []
            if current is not None:
                checks.append(last_current == current)
            if failover is not None:
                checks.append(last_failover == failover)
            if all(checks):
                return evc
            if time.time() >= deadline:
                break
            time.sleep(2)
        raise AssertionError(
            f"EVC {evc_id} path mismatch.\n"
            f"  expected current={current} failover={failover}\n"
            f"  observed current={last_current} failover={last_failover}\n"
            f"{self.dump_evc_state(evc_id)}\n"
            f"{self.dump_nni_state()}"
        )

    def wait_evc_failover_ready(self, evc_id, timeout=90):
        """Wait until mef_eline has finished provisioning an EVC's failover path.

        Enabling INT before this point is a race telemetry_int loses silently.
        ``enable`` builds its INT flows from the mef_eline flows that exist at
        that moment, so a failover deployed afterwards is left to
        ``handle_failover_flows`` -- which drops the EVC when
        ``has_int_enabled`` is still false on the event payload
        (``managers/int.py:751``), the telemetry metadata write not having
        landed yet. The failover INT subset is then never installed and nothing
        retries it, so ``check_consistency`` reports those flows missing for as
        long as the EVC lives.

        Returns at once for the EVCs that never get a pre-provisioned failover:
        static ones, and intra-switch ones (no links in ``current_path``, so
        there is no alternative path to pre-provision).
        """
        deadline = time.time() + timeout
        while True:
            evc = self.get_evc(evc_id)
            if not evc.get("dynamic_backup_path"):
                return evc
            if evc.get("active") and not evc.get("current_path"):
                return evc
            if evc.get("failover_path"):
                return evc
            if time.time() >= deadline:
                raise AssertionError(
                    f"EVC {evc_id} never got a failover path.\n"
                    f"{self.dump_evc_state(evc_id)}\n{self.dump_nni_state()}"
                )
            time.sleep(2)

    def count_all_evc_flows(self, switch):
        """Return ``(n_mef_flows, n_int_flows)`` across every EVC on a switch."""
        mef = n_int = 0
        for line in switch.dpctl("dump-flows").splitlines():
            flow = self._parse_flow_line(line)
            if flow is None:
                continue
            prefix = flow["cookie"] >> 56
            if prefix == self.mef_cookie_prefix:
                mef += 1
            elif prefix == self.int_cookie_prefix:
                n_int += 1
        return mef, n_int

    def wait_no_evc_flows(self, switches, timeout=60):
        """Wait until no mef_eline or telemetry_int flows remain on any switch.

        Used by the light ``setup_method`` reset in the suites that do not restart
        kytos between tests.
        """
        deadline = time.time() + timeout
        observed = {}
        while True:
            observed = {sw.name: self.count_all_evc_flows(sw) for sw in switches}
            if not any(sum(counts) for counts in observed.values()):
                return observed
            if time.time() >= deadline:
                raise AssertionError(f"EVC flows still present: {observed}")
            time.sleep(2)

    def assert_compare_clean(self, timeout=30):
        """Wait until ``GET /v1/evc/compare`` reports nothing to reconcile.

        ``compare`` reads EVC metadata and stored flows independently, so right
        after a change it can briefly report a disagreement that is about to
        resolve itself.
        """
        deadline = time.time() + timeout
        compared = None
        while True:
            compared = self.int_compare()
            if compared == []:
                return compared
            if time.time() >= deadline:
                raise AssertionError(f"compare never cleared: {compared}")
            time.sleep(2)

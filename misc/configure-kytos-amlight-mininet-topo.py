#!/usr/bin/env python3
"""Configure Kytos for the AmLightTopo Mininet topology."""

import os
import sys
import requests


# DPID suffix, Kytos node name, site address, and switch-side ports.
SWITCHES = (
    ('11', 'Ampath1', 'Equinix MI1, Miami, FL', (1, 2, 3, 4, 5, 15, 16)),
    ('12', 'Ampath2', 'Equinix MI1, Miami, FL', (1, 3, 4, 5, 6, 15, 16)),
    ('13', 'SoL2', 'Equinix SP4, Sao Paulo, Brazil', (1, 2, 4, 5, 16)),
    ('14', 'SanJuan', 'Hub787, San Juan, Puerto Rico', (3, 6, 16)),
    ('15', 'AndesLight2', 'CenturyLink, Santiago, Chile', (1, 3, 5, 15, 16)),
    ('16', 'AndesLight3', 'CenturyLink, Santiago, Chile', (1, 2, 16)),
    ('17', 'Ampath3', 'Equinix MI1, Miami, FL', (2, 3, 16)),
    ('18', 'Ampath4', 'Equinix MI3, Boca Raton, FL', (1, 2, 3, 4, 16)),
    ('19', 'Ampath5', 'Equinix MI1, Miami, FL', (1, 4, 5, 16)),
    ('20', 'Ampath7', 'Equinix MI1, Miami, FL', (1, 2, 16)),
    ('21', 'JAX1', 'CenturyLink, Jacksonville, FL', (1, 4, 16)),
    ('22', 'JAX2', 'CenturyLink, Jacksonville, FL', (1, 5, 16)),
)


# Precomputed Kytos link IDs and display names.
LINKS = (
    # DPID suffixes 11 and 12, port 1 on both switches.
    ('b6dac3f36cb8e7dd72e3e49746b0ea19035fb9e7e57e117fe4bfbfb774851c4d', 'MIA-Ampath1-Ampath2-01'),
    # DPID suffixes 11 and 17, port 2 on both switches.
    ('48457fc028484b039a270942671a8edc3ee4f1878585c469fc2a609505330e4d', 'MIA-Ampath1-Ampath3-01'),
    # DPID suffixes 11 and 18, port 3 on both switches.
    ('73907de73db97005397e8d60739b2941e172d510827214c2f90152b3e97c0dee', 'MIA-BCT-01'),
    # DPID suffixes 11 and 13, port 4 on both switches.
    ('f80f99546ad6e98baecc0f9811ae5eaa1801929bb7c07bcf315d417b25e8165a', 'MIA-SAO-01'),
    # DPID suffixes 11 and 13, port 5 on both switches.
    ('324c05f85603619312bd9fc03e60ae98b35f1d0b9bb3884c49ad31df5f37dfd3', 'MIA-SAO-02'),
    # DPID suffixes 12 and 17, port 3 on both switches.
    ('a6369b70448345f1387a40c82d8767c9601cc26f7e88574875932c712349b7fc', 'MIA-Ampath2-Ampath3-01'),
    # DPID suffixes 12 and 19, port 4 on both switches.
    ('c528756e19b964d1355d0f9d4ae0ac46062f28b98b7c170a378f6996ba9765eb', 'MIA-Ampath2-Ampath5-01'),
    # DPID suffixes 12 and 15, port 5 on both switches.
    ('ed22424dfe2f40448fdedf899e38eb70883a535c3692e8f970c66a5d1790faee', 'MIA-SCL-01'),
    # DPID suffixes 12 and 14, port 6 on both switches.
    ('a878bba10fc48bed370f1de720cdcb1d840f904614b87d3372f69956439fa7d0', 'MIA-SJU-01'),
    # DPID suffixes 18 and 19, port 1 on both switches.
    ('1f794712678b3795518c1e5eeacbcf35d99ed181135867a44a9f4950f058818f', 'MIA-BCT-02'),
    # DPID suffixes 18 and 20, port 2 on both switches.
    ('0f1e4e584f6df744797e8e44581a3378959cf4eed86cbe6f4dd16630358cfcd4', 'MIA-BCT-03'),
    # DPID suffixes 18 and 21, port 4 on both switches.
    ('e59054b71c1f93b3ffb19eead5b5b3d040aa00d616071a39689ab2d1cf0936a4', 'BCT-JAX-01'),
    # DPID suffixes 19 and 22, port 5 on both switches.
    ('b4e55d8b62413d5bfc7a98a65147ce6c0063e0d412f7cddf9d29a3cdbe2db9d0', 'MIA-JAX-01'),
    # DPID suffixes 20 and 13, port 1 on both switches.
    ('49894a364d2dd2748939ad77bba497a3ecbf210372992bc4e3ae362068319025', 'MIA-SAO-03'),
    # DPID suffixes 13 and 16, port 2 on both switches.
    ('988218dda2b6ff47d4a9d5f7b8cd1b27997e5173fadf75f95a736e76e2dc8262', 'SAO-SCL-01'),
    # DPID suffixes 16 and 15, port 1 on both switches.
    ('722ae748db21c07064d88efa0624d8b7fbaea92b52a9ea46e4b005e0dc9cbe15', 'SCL-AndesLight2-AndesLight3-01'),
    # DPID suffixes 15 and 14, port 3 on both switches.
    ('ff64d5c598500606b83142d58003df121cf83736d050cc83d01e5de549a7b8a7', 'SCL-SJU-01'),
    # DPID suffixes 21 and 22, port 1 on both switches.
    ('a0a4108c8ec1c7c24f9dccc146fc09db0054e48164c75b4e16fd2b4ccf0aa3c4', 'JAX-JAX1-JAX2-01'),
)


def post(session, base_url, path, metadata=None):
    url = f"{base_url}/{path}"
    try:
        response = session.post(
            url, json=metadata, timeout=30, allow_redirects=False
        )
        if not 200 <= response.status_code < 300:
            detail = response.text[:1024].strip()
            message = f"POST {url} returned HTTP {response.status_code}"
            raise RuntimeError(f"{message}: {detail}" if detail else message)
    except requests.RequestException as exc:
        raise RuntimeError(f"POST {url} failed: {exc}") from exc


def main():
    base_url = os.environ.get("TOPOLOGY_API", "").strip().rstrip("/")
    if not base_url:
        print("Set TOPOLOGY_API to the Kytos topology API base URL.", file=sys.stderr)
        return 1

    try:
        with requests.Session() as session:
            session.headers["Content-Type"] = "application/json"
            for suffix, name, address, ports in SWITCHES:
                dpid = f"00:00:00:00:00:00:00:{suffix}"
                switch = f"switches/{dpid}"
                post(session, base_url, f"{switch}/enable")
                post(session, base_url, f"{switch}/metadata", {"node_name": name})
                post(session, base_url, f"{switch}/metadata", {"address": address})
                for port in ports:
                    interface = f"interfaces/{dpid}:{port}"
                    post(session, base_url, f"{interface}/enable")
                    post(
                        session, base_url, f"{interface}/metadata",
                        {"mtu": 9000, "port_name": f"eth{port}"},
                    )
            for link_id, link_name in LINKS:
                link = f"links/{link_id}"
                post(
                    session, base_url, f"{link}/metadata",
                    {
                        "bandwidth": 100,
                        "link_name": link_name,
                        "availability": 100,
                        "delay": 1,
                        "packet_loss": 0,
                        "utilization": 0,
                        "priority": 10,
                    },
                )
                post(session, base_url, f"{link}/enable")
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

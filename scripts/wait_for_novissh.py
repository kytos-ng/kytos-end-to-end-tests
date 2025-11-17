import paramiko
import time
import os
import sys

def test_ssh_connection(hostname, username, password):
    """
    Tests an SSH connection to a remote host using Paramiko.
    Returns:
        bool: True if the connection is successful, False otherwise.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname, port=22, username=username, password=password)
        return True
    except Exception as e:
        return False
    finally:
        client.close()


def novissh_wait(maxwait=300):
    SWITCHES = os.environ.get("NOVISWITCHES", "").split()
    NOVIUSER = os.environ.get("NOVIUSER")
    NOVIPASS = os.environ.get("NOVIPASS")
    if not SWITCHES or not NOVIUSER or not NOVIPASS:
        raise ValueError("Missing one of the parameters switches: NOVISWITCHES, NOVIUSER or NOVIPASS")

    started = time.time()
    while time.time() - started < maxwait:
        for sw in SWITCHES[:]:
            if test_ssh_connection(sw, NOVIUSER, NOVIPASS):
                SWITCHES.remove(sw)
        if not SWITCHES:
            print("All switches connected")
            break
        print(f"Pending switches: {SWITCHES}. Sleeping for 5s...")
        time.sleep(5)


if __name__ == "__main__":
    print("Trying to run hello command on Noviflow switches via SSH...")
    maxwait = 600
    if len(sys.argv) > 1:
        maxwait = int(sys.argv[1])

    novissh_wait(maxwait=maxwait)

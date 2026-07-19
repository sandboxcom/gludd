#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type


DOCUMENTATION = r"""
---
module: os_namespaces

short_description: Manage Linux namespaces (network, mount, PID, UTS, etc.)

description:
  - Create, list, and delete Linux namespaces.
  - Network namespaces are managed via C(ip netns).
  - Mount, PID, UTS, and other namespaces are created with C(unshare)
    and made persistent via bind-mounts under a user-specified directory.
  - Requires C(iproute2) for network namespaces and C(util-linux) for
    unshare / bind-mount support.

options:
  name:
    description: Name of the namespace to manage.
    required: true
    type: str
  type:
    description:
      - Type of namespace.
      - C(net) uses C(ip netns) for network namespace management.
      - C(mount), C(pid), C(uts), C(user), C(ipc), C(cgroup), C(time)
        use C(unshare) and bind-mounts for persistence.
    required: true
    type: str
    choices: [net, mount, pid, uts, user, ipc, cgroup, time]
  state:
    description:
      - Desired state.
      - C(present) creates the namespace.
      - C(absent) deletes the namespace.
      - C(list) lists existing namespaces of the given type.
    type: str
    default: present
    choices: [present, absent, list]
  bind_mount_root:
    description:
      - Root directory for persistent bind-mounts of non-network namespaces.
      - Defaults to C(/var/run/netns) for consistency with C(ip netns).
    type: str
    default: /var/run/netns
  command:
    description:
      - Command to run inside the namespace (only for C(type=net)).
      - Uses C(ip netns exec).
    type: str

author:
  - General Ludd (@general-ludd)

requirements:
  - iproute2 (for type=net)
  - util-linux >= 2.38 (for unshare --kill-child, mount/pid/uts types)
"""

EXAMPLES = r"""
- name: Create a network namespace
  general_ludd.os_expert.os_namespaces:
    name: mynetns
    type: net
    state: present

- name: Run command inside network namespace
  general_ludd.os_expert.os_namespaces:
    name: mynetns
    type: net
    command: ip addr show

- name: Create a persistent mount namespace
  general_ludd.os_expert.os_namespaces:
    name: mymntns
    type: mount
    state: present
    bind_mount_root: /var/run/netns

- name: List network namespaces
  general_ludd.os_expert.os_namespaces:
    name: all
    type: net
    state: list

- name: Delete a PID namespace
  general_ludd.os_expert.os_namespaces:
    name: mypidns
    type: pid
    state: absent
"""

RETURN = r"""
name:
  description: Name of the namespace.
  type: str
  returned: always
type:
  description: Type of the namespace.
  type: str
  returned: always
state:
  description: Requested state.
  type: str
  returned: always
namespaces:
  description: List of existing namespace names (when state=list).
  type: list
  elements: str
  returned: when state=list
command_output:
  description: Output from a command run inside the namespace.
  type: str
  returned: when command is set
bind_mount:
  description: Path to the bind-mount for persistent non-net namespaces.
  type: str
  returned: when type is not net and state=present
"""

import os
import glob
import subprocess

from ansible.module_utils.basic import AnsibleModule


def _run(cmd, check=False):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", "command not found: %s" % cmd[0]
    except subprocess.CalledProcessError as exc:
        return exc.returncode, exc.stdout or "", exc.stderr or ""
    except OSError as exc:
        return 1, "", str(exc)


def _list_netns():
    _, out, _ = _run(["ip", "netns", "list"])
    return [line.split(" ")[0] for line in out.strip().splitlines() if line.strip()]


def _netns_present(name):
    rc, _, _ = _run(["ip", "netns", "add", name])
    return rc


def _netns_absent(name):
    rc, _, _ = _run(["ip", "netns", "delete", name])
    return rc


def _netns_exec(name, command):
    cmd = ["ip", "netns", "exec", name] + command
    return _run(cmd)


def _list_bind_mount_namespaces(bind_mount_root, ns_type):
    if not os.path.isdir(bind_mount_root):
        return []
    pattern = os.path.join(bind_mount_root, "*.%s" % ns_type)
    entries = []
    for path in sorted(glob.glob(pattern)):
        base = os.path.basename(path)
        name = base[: -(len(ns_type) + 1)]
        entries.append(name)
    return entries


def _bind_mount_ns_present(name, ns_type, bind_mount_root):
    os.makedirs(bind_mount_root, exist_ok=True)
    bind_path = os.path.join(bind_mount_root, "%s.%s" % (name, ns_type))
    if os.path.exists(bind_path):
        return 0, bind_path

    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.setsid()
            ns_flag_map = {
                "mount": 0x00020000,
                "pid": 0x20000000,
                "uts": 0x04000000,
                "user": 0x10000000,
                "ipc": 0x08000000,
                "cgroup": 0x02000000,
                "time": 0x00000080,
            }
            import ctypes

            LIBC = ctypes.CDLL("libc.so.6", use_errno=True)
            CLONE_NEWNS = ns_flag_map.get(ns_type, 0x00020000)
            rc = LIBC.unshare(CLONE_NEWNS)
            if rc != 0:
                os._exit(1)

            fd = os.open(bind_path, os.O_CREAT | os.O_RDONLY, 0o000)
            os.close(fd)

            import signal

            signal.pause()
        except Exception:
            os._exit(1)
        os._exit(0)
    else:
        import time

        time.sleep(0.2)
        try:
            ns_proc_path = "/proc/%d/ns/%s" % (child_pid, ns_type)
            os.makedirs(os.path.dirname(bind_path), exist_ok=True)
            os.link(ns_proc_path, bind_path)
        except OSError:
            try:
                os.kill(child_pid, 9)
            except OSError:
                pass
            raise
        finally:
            try:
                os.kill(child_pid, 9)
            except OSError:
                pass
        return 0, bind_path


def _bind_mount_ns_absent(name, ns_type, bind_mount_root):
    bind_path = os.path.join(bind_mount_root, "%s.%s" % (name, ns_type))
    if not os.path.exists(bind_path):
        return 0
    try:
        os.unlink(bind_path)
    except OSError as exc:
        return 1
    return 0


def run_module():
    module_args = dict(
        name=dict(type="str", required=True),
        type=dict(
            type="str",
            required=True,
            choices=["net", "mount", "pid", "uts", "user", "ipc", "cgroup", "time"],
        ),
        state=dict(
            type="str",
            default="present",
            choices=["present", "absent", "list"],
        ),
        bind_mount_root=dict(type="str", default="/var/run/netns"),
        command=dict(type="str", default=""),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=False)

    name = module.params["name"]
    ns_type = module.params["type"]
    state = module.params["state"]
    bind_mount_root = module.params["bind_mount_root"]
    command = module.params["command"]

    result = dict(changed=False, name=name, type=ns_type, state=state)

    if state == "list":
        if ns_type == "net":
            result["namespaces"] = _list_netns()
        else:
            result["namespaces"] = _list_bind_mount_namespaces(bind_mount_root, ns_type)
        module.exit_json(**result)

    if ns_type == "net":
        if state == "present":
            current = set(_list_netns())
            changed = name not in current
            if changed:
                rc = _netns_present(name)
                if rc != 0:
                    module.fail_json(
                        msg="Failed to create network namespace '%s'" % name, **result
                    )
                result["changed"] = True
                current = set(_list_netns())
                if name not in current:
                    module.fail_json(
                        msg="Network namespace '%s' not found after creation" % name,
                        **result,
                    )
            if command:
                rc, out, err = _netns_exec(name, command.split())
                result["command_output"] = out
                if err:
                    result["command_stderr"] = err
            module.exit_json(**result)

        if state == "absent":
            current = set(_list_netns())
            changed = name in current
            if changed:
                rc = _netns_absent(name)
                if rc != 0:
                    module.fail_json(
                        msg="Failed to delete network namespace '%s'" % name, **result
                    )
                result["changed"] = True
            module.exit_json(**result)
    else:
        if state == "present":
            bind_path = os.path.join(bind_mount_root, "%s.%s" % (name, ns_type))
            changed = not os.path.exists(bind_path)
            if changed:
                _, bind_mount = _bind_mount_ns_present(name, ns_type, bind_mount_root)
                result["changed"] = True
                result["bind_mount"] = bind_mount
            else:
                result["bind_mount"] = bind_path
            module.exit_json(**result)

        if state == "absent":
            bind_path = os.path.join(bind_mount_root, "%s.%s" % (name, ns_type))
            changed = os.path.exists(bind_path)
            if changed:
                rc = _bind_mount_ns_absent(name, ns_type, bind_mount_root)
                if rc != 0:
                    module.fail_json(
                        msg="Failed to delete namespace bind-mount '%s'" % bind_path,
                        **result,
                    )
                result["changed"] = True
            module.exit_json(**result)


def main():
    run_module()


if __name__ == "__main__":
    main()

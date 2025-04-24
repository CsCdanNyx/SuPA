#  Copyright 2023 ESnet / UCSD / SURF.
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""
Arista Backend for EOS 4.x.
Copied from paristaEOS4.py and adapted for Arista EOS4.

Swith command prompt starts with ceos2# and config mode is ceos2(config)#

Configuration:
You need to first enable the switch cli for specified privilege commands level (Not shown in paristaEOS4.py).
ceos2>enable

To setup a VLAN connection:
// in sw cli mode on login
ceos2# configure
// config for source interface
ceos2(config)#vlan {$vlan}
ceos2(config)#exit
ceos2(config)#interface {$src_interface}
ceos2(config-if-{$et?})#switchport mode trunk
ceos2(config-if-{$et?})#switchport trunk allowed vlan add {$vlan}
ceos2(config-if-{$et?})#exit
// redo config for destination interface
ceos2(config)# interface {$dst_interface}
ceos2(config-if-{$et?})#switchport mode trunk
ceos2(config-if-{$et?})#switchport trunk allowed vlan add {$vlan}
ceos2(config-if-{$et?})#exit
// Saving config
ceos2#write

teardown:
// in sw cli mode on login
ceos2# configure
// rm vlan for source interface
ceos2(config)#interface {$src_interface}
ceos2(config-if-{$et?})#switchport trunk allowed vlan remove {$vlan}
ceos2(config-if-{$et?})#exit
// rm vlan for destination interface
ceos2(config)#interface {$dst_interface}
ceos2(config-if-{$et?})#switchport trunk allowed vlan remove {$vlan}
ceos2(config-if-{$et?})#exit
// Saving config
ceos2#write

// no use
# ceos2(config)#no vlan {$vlan}
# ceos2#copy running-config startup-config

"""
import os
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import paramiko
import yaml
from pydantic import BaseSettings

from supa.connection.error import GenericRmError
from supa.job.shared import NsiException
from supa.nrm.backend import STP, BaseBackend
from supa.util.find import find_file


class BackendSettings(BaseSettings):
    """Backend settings with default values."""

    # SSH connection settings
    ssh_hostname: str = "localhost"
    ssh_port: int = 22
    ssh_host_fingerprint: str = ""
    ssh_username: str = ""
    ssh_password: str = ""
    ssh_private_key_path: str = ""
    ssh_public_key_path: str = ""

    # CLI interaction settings
    cli_prompt: str = ""
    cli_needs_enable: bool = True

    # Configuration files
    stps_config: str = "stps_config.yml"

    # Command templates - these can be overridden in the env file
    cmd_enable: str = "enable"
    cmd_configure: str = "configure"
    cmd_create_vlan: str = "vlan %i"
    cmd_delete_vlan: str = "no vlan %i"
    cmd_interface: str = "interface %s"
    cmd_mode_access: str = "switchport mode access"
    cmd_mode_trunk: str = "switchport mode trunk"
    cmd_access_vlan: str = "switchport access vlan %i"
    cmd_trunk_add_vlan: str = "switchport trunk allowed vlan add %i"
    cmd_trunk_rem_vlan: str = "switchport trunk allowed vlan remove %i"
    cmd_exit: str = "exit"
    cmd_commit: str = "write"
    cmd_no_shutdown: str = "no shutdown"


class Backend(BaseBackend):
    """Arista EOS backend interface using Paramiko."""

    def __init__(self) -> None:
        """Load backend properties from env file."""
        super(Backend, self).__init__()

        # Get backend name from the filename to make code more portable
        self.backend_name = os.path.basename(__file__).split(".")[0]
        self.configs_dir = f"{self.backend_name}_configs"

        # Load backend settings from environment file
        env_file = find_file(f"{self.configs_dir}/{self.backend_name}.env")
        self.backend_settings = BackendSettings(_env_file=env_file)
        self.log.info("Read backend properties", path=str(env_file))

        # Store commands for easy access
        self.commands = {
            "enable": self.backend_settings.cmd_enable.encode("utf-8"),
            "configure": self.backend_settings.cmd_configure.encode("utf-8"),
            "create_vlan": self.backend_settings.cmd_create_vlan.encode("utf-8"),
            "delete_vlan": self.backend_settings.cmd_delete_vlan.encode("utf-8"),
            "interface": self.backend_settings.cmd_interface.encode("utf-8"),
            "mode_access": self.backend_settings.cmd_mode_access.encode("utf-8"),
            "mode_trunk": self.backend_settings.cmd_mode_trunk.encode("utf-8"),
            "access_vlan": self.backend_settings.cmd_access_vlan.encode("utf-8"),
            "trunk_add_vlan": self.backend_settings.cmd_trunk_add_vlan.encode("utf-8"),
            "trunk_rem_vlan": self.backend_settings.cmd_trunk_rem_vlan.encode("utf-8"),
            "exit": self.backend_settings.cmd_exit.encode("utf-8"),
            "commit": self.backend_settings.cmd_commit.encode("utf-8"),
            "no_shutdown": self.backend_settings.cmd_no_shutdown.encode("utf-8"),
        }

    def _get_ssh_shell(self) -> None:
        """Initialize SSH connection and shell."""
        self.sshclient = paramiko.SSHClient()
        self.sshclient.load_system_host_keys()
        self.sshclient.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        privkey = None

        try:
            if self.backend_settings.ssh_private_key_path:
                if os.path.exists(self.backend_settings.ssh_private_key_path):
                    privkey = paramiko.RSAKey.from_private_key_file(self.backend_settings.ssh_private_key_path)
                elif os.path.exists(os.path.expanduser(self.backend_settings.ssh_private_key_path)):
                    privkey = paramiko.RSAKey.from_private_key_file(
                        os.path.expanduser(self.backend_settings.ssh_private_key_path)
                    )
                else:
                    reason = "Incorrect private key path or file does not exist"
                    self.log.warning("Failed to initialize SSH client", reason=reason)
                    raise NsiException(GenericRmError, reason)

            if privkey:
                self.sshclient.connect(
                    hostname=self.backend_settings.ssh_hostname,
                    port=self.backend_settings.ssh_port,
                    username=self.backend_settings.ssh_username,
                    pkey=privkey,
                )
            elif self.backend_settings.ssh_password:
                self.sshclient.connect(
                    hostname=self.backend_settings.ssh_hostname,
                    port=self.backend_settings.ssh_port,
                    username=self.backend_settings.ssh_username,
                    password=self.backend_settings.ssh_password,
                )
            else:
                raise AssertionError("No keys or password supplied")

        except Exception as exception:
            self.log.warning("SSH client connect failure", reason=str(exception))
            raise NsiException(GenericRmError, str(exception)) from exception

        transport = self.sshclient.get_transport()
        transport.set_keepalive(30)  # type: ignore[union-attr]
        self.channel = self.sshclient.invoke_shell()
        self.channel.settimeout(30)

    def _close_ssh_shell(self) -> None:
        """Close SSH connection and shell."""
        self.channel.close()
        self.sshclient.close()

    def _create_configure_commands(self, source_port: str, dest_port: str, vlan: int) -> List[bytes]:
        """Generate commands to configure VLAN on source and destination ports.

        Args:
            source_port: Source port identifier
            dest_port: Destination port identifier
            vlan: VLAN number to configure

        Returns:
            List of commands to execute
        """
        cmds = self.commands

        # Generate all the commands using the templates
        createvlan = cmds["create_vlan"] % vlan
        intsrc = cmds["interface"] % source_port.encode("utf-8")
        intdst = cmds["interface"] % dest_port.encode("utf-8")
        modetrunk = cmds["mode_trunk"]
        addvlan = cmds["trunk_add_vlan"] % vlan
        cmdexit = cmds["exit"]

        # Build command sequence
        commands = [createvlan, cmdexit, intsrc, modetrunk, addvlan, cmdexit, intdst, modetrunk, addvlan, cmdexit]
        return commands

    def _create_delete_commands(self, source_port: str, dest_port: str, vlan: int) -> List[bytes]:
        """Generate commands to remove VLAN from source and destination ports.

        Args:
            source_port: Source port identifier
            dest_port: Destination port identifier
            vlan: VLAN number to remove

        Returns:
            List of commands to execute
        """
        cmds = self.commands

        # Generate all the commands using the templates
        intsrc = cmds["interface"] % source_port.encode("utf-8")
        intdst = cmds["interface"] % dest_port.encode("utf-8")
        remvlan = cmds["trunk_rem_vlan"] % vlan
        cmdexit = cmds["exit"]

        # Build command sequence
        commands = [intsrc, remvlan, cmdexit, intdst, remvlan, cmdexit]
        return commands

    def _send_commands(self, commands: List[bytes]) -> None:
        """Send commands to the switch via SSH.

        Args:
            commands: List of commands to execute

        Raises:
            NsiException: If there's an error sending commands
        """
        line = b""
        line_termination = b"\r"  # line termination
        self._get_ssh_shell()

        self.log.debug("Sending commands to switch", command_list=commands)

        try:
            self.log.debug("Establishing SSH connection")

            if self.backend_settings.cli_needs_enable:
                while not line.decode("utf-8").endswith(self.backend_settings.cli_prompt + ">"):
                    resp = self.channel.recv(999)
                    line += resp
                    self.log.debug(resp)
                line = b""

                # Enable Privileged Mode
                self.log.debug("Enable Privileged Mode")
                self.channel.send(self.commands["enable"] + line_termination)

            while not line.decode("utf-8").endswith(self.backend_settings.cli_prompt + "#"):
                resp = self.channel.recv(999)
                line += resp
                self.log.debug(resp)
            line = b""

            self.log.debug("Starting configuration")
            self.channel.send(self.commands["configure"] + line_termination)
            while not line.decode("utf-8").endswith(self.backend_settings.cli_prompt + "(config)#"):
                resp = self.channel.recv(999)
                line += resp
                self.log.debug(resp)
            line = b""

            self.log.debug("Entered configure mode")
            for cmd in commands:
                self.log.debug("Command: {}".format(cmd))
                self.channel.send(cmd + line_termination)
                while not line.decode("utf-8").endswith(")#"):
                    resp = self.channel.recv(999)
                    line += resp
                    self.log.debug(resp)
                line = b""

            self.log.debug("Exiting configure mode")
            self.channel.send(self.commands["exit"] + line_termination)
            while not line.decode("utf-8").endswith(self.backend_settings.cli_prompt + "#"):
                resp = self.channel.recv(999)
                line += resp
                self.log.debug(resp)
            line = b""

            self.log.debug("Saving configuration")
            self.channel.send(self.commands["commit"] + line_termination)
            while not line.decode("utf-8").endswith(self.backend_settings.cli_prompt + "#"):
                resp = self.channel.recv(999)
                line += resp
                self.log.debug(resp)

        except Exception as exception:
            self._close_ssh_shell()
            self.log.warning("Error sending commands", exception=str(exception))
            raise NsiException(GenericRmError, "Error sending commands: {}".format(str(exception))) from exception

        self._close_ssh_shell()
        self.log.debug("Commands successfully committed")

    def activate(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Activate resources by configuring VLANs on the switch.

        Args:
            connection_id: Unique identifier for the connection
            bandwidth: Required bandwidth in Mbps
            src_port_id: Source port identifier
            src_vlan: Source VLAN number
            dst_port_id: Destination port identifier
            dst_vlan: Destination VLAN number
            circuit_id: Circuit identifier

        Returns:
            Circuit identifier (generated if not provided)

        Raises:
            NsiException: If VLANs don't match
        """
        self.log.info(
            "Activate resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="activate",
            connection_id=str(connection_id),
        )

        if src_vlan != dst_vlan:
            raise NsiException(GenericRmError, "VLANs must match")

        try:
            self._send_commands(self._create_configure_commands(src_port_id, dst_port_id, dst_vlan))

            # Generate circuit ID if not provided
            if not circuit_id:
                circuit_id = uuid4().urn

            self.log.info(
                "Link up",
                src_port_id=src_port_id,
                dst_port_id=dst_port_id,
                src_vlan=src_vlan,
                dst_vlan=dst_vlan,
                circuit_id=circuit_id,
            )
            return circuit_id
        except Exception as e:
            self.log.error("Failed to activate connection", error=str(e))
            raise NsiException(GenericRmError, "Failed to activate connection: {}".format(str(e))) from e

    def deactivate(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Deactivate resources by removing VLANs from the switch.

        Args:
            connection_id: Unique identifier for the connection
            bandwidth: Required bandwidth in Mbps
            src_port_id: Source port identifier
            src_vlan: Source VLAN number
            dst_port_id: Destination port identifier
            dst_vlan: Destination VLAN number
            circuit_id: Circuit identifier
        """
        self.log.info(
            "Deactivate resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="deactivate",
            connection_id=str(connection_id),
        )

        self._send_commands(self._create_delete_commands(src_port_id, dst_port_id, dst_vlan))

        self.log.info(
            "Link down",
            src_port_id=src_port_id,
            dst_port_id=dst_port_id,
            src_vlan=src_vlan,
            dst_vlan=dst_vlan,
            circuit_id=circuit_id,
        )
        return None

    def topology(self) -> List[STP]:
        """Read STPs from YAML file and convert to STP objects."""
        self.log.info(
            "Get topology from {} NRM".format(self.backend_name), backend=self.__module__, primitive="topology"
        )

        # Find and load the STP configuration file
        config_path = find_file(f"{self.configs_dir}/{self.backend_settings.stps_config}")

        # Load and process STPs
        with open(config_path, "r") as f:
            stp_configs = yaml.safe_load(f)["stps"]
            return [self._process_stp_config(config) for config in stp_configs]

    def _process_stp_config(self, config: dict) -> STP:
        """Convert a single STP configuration to an STP object.

        Handles both bidirectional and directional STP configurations.

        Args:
            config: STP configuration dictionary

        Returns:
            STP object
        """
        # Create a copy to avoid modifying the original
        processed = config.copy()

        # Process bidirectional STP configuration
        if "remote_stp" in processed:
            remote = processed.pop("remote_stp")
            prefix = remote["prefix_urn"]
            id = remote["id"]

            processed["is_alias_in"] = f"{prefix}:{id}:out"
            processed["is_alias_out"] = f"{prefix}:{id}:in"

        # Process directional in/out configurations
        if "remote_stp_in" in processed:
            remote = processed.pop("remote_stp_in")
            processed["is_alias_in"] = f"{remote['prefix_urn']}:{remote['id']}"

        if "remote_stp_out" in processed:
            remote = processed.pop("remote_stp_out")
            processed["is_alias_out"] = f"{remote['prefix_urn']}:{remote['id']}"

        return STP(**processed)

    ### Not implemented functions, provide logging only ###

    def reserve(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
    ) -> Optional[str]:
        """Reserve resources in NRM."""
        self.log.info(
            "Reserve resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve",
            connection_id=str(connection_id),
        )
        return None

    def reserve_timeout(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Reserve timeout resources in NRM."""
        self.log.info(
            "Reserve timeout resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve_timeout",
            connection_id=str(connection_id),
        )
        return None

    def reserve_commit(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Reserve commit resources in NRM."""
        self.log.info(
            "Reserve commit resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve_commit",
            connection_id=str(connection_id),
        )
        return None

    def reserve_abort(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Reserve abort resources in NRM."""
        self.log.info(
            "Reserve abort resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="reserve_abort",
            connection_id=str(connection_id),
        )
        return None

    def provision(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Provision resources in NRM."""
        self.log.info(
            "Provision resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="provision",
            connection_id=str(connection_id),
        )
        return None

    def release(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Release resources in NRM."""
        self.log.info(
            "Release resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="release",
            connection_id=str(connection_id),
        )
        return None

    def terminate(
        self,
        connection_id: UUID,
        bandwidth: int,
        src_port_id: str,
        src_vlan: int,
        dst_port_id: str,
        dst_vlan: int,
        circuit_id: str,
    ) -> Optional[str]:
        """Terminate resources in NRM."""
        self.log.info(
            "Terminate resources in {} NRM".format(self.backend_name),
            backend=self.__module__,
            primitive="terminate",
            connection_id=str(connection_id),
        )
        return None

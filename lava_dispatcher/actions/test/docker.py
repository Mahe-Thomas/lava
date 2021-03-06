# Copyright (C) 2020 Linaro Limited
#
# Author: Antonio Terceiro <antonio.terceiro@linaro.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

import os
import shlex

from lava_common.exceptions import LAVATimeoutError
from lava_dispatcher.action import Action, Pipeline
from lava_dispatcher.actions.deploy.overlay import CreateOverlay
from lava_dispatcher.actions.test.shell import TestShellAction
from lava_dispatcher.logical import LavaTest
from lava_dispatcher.power import ReadFeedback
from lava_dispatcher.shell import ShellCommand, ShellSession
from lava_dispatcher.utils.docker import DockerRun
from lava_dispatcher.utils.udev import get_udev_devices
from lava_dispatcher.utils.udev import WaitDeviceBoardID
from lava_dispatcher_host import add_device_container_mapping


class DockerTest(LavaTest):
    """
    DockerTest Strategy object
    """

    priority = 10

    @classmethod
    def action(cls, parameters):
        return DockerTestAction()

    @classmethod
    def accepts(cls, device, parameters):
        if "definition" in parameters or "definitions" in parameters:
            if "docker" in parameters:
                return True, "accepted"
        return False, "docker or definition(s) not in parameters"

    @classmethod
    def needs_deployment_data(cls, parameters):
        return False

    @classmethod
    def needs_overlay(cls, parameters):
        return True

    @classmethod
    def has_shell(cls, parameters):
        return True


class GetBoardId:
    def get_board_id(self):
        device_info = self.job.device.get("device_info")
        if not device_info:
            return None
        return device_info[0].get("board_id")


class DockerTestAction(Action, GetBoardId):
    name = "lava-docker-test"
    description = "Runs tests in a docker container"
    summary = "Runs tests in a docker container, with the DUT available via adb/fastboot over USB"
    timeout_exception = LAVATimeoutError

    def populate(self, parameters):
        self.pipeline = Pipeline(parent=self, job=self.job, parameters=parameters)
        self.pipeline.add_action(DockerTestSetEnvironment())
        self.pipeline.add_action(CreateOverlay())
        wait_for_device = (
            parameters.get("docker", {}).get("wait", {}).get("device", True)
        )
        if wait_for_device:
            self.pipeline.add_action(WaitDeviceBoardID(self.get_board_id()))
        self.pipeline.add_action(DockerTestShell(wait_for_device))
        self.pipeline.add_action(ReadFeedback())


class DockerTestSetEnvironment(Action, GetBoardId):
    name = "lava-docker-test-set-environment"
    description = "Adds necessary environments variables for docker-test-shell"
    summary = description
    timeout_exception = LAVATimeoutError

    def run(self, connection, max_end_time):
        environment = self.job.parameters.get("environment", {})
        environment["ANDROID_SERIAL"] = self.get_board_id()

        connections = self.job.device.get("commands", {}).get("connections", {})
        for name, command in connections.items():
            connect = command.get("connect")
            tags = command.get("tags", [])
            if connect:
                k = "LAVA_CONNECTION_COMMAND_%s" % name.upper()
                environment[k] = connect
                if "primary" in tags:
                    environment["LAVA_CONNECTION_COMMAND"] = connect

        self.job.parameters["environment"] = environment
        connection = super().run(connection, max_end_time)
        return connection


class DockerTestShell(TestShellAction, GetBoardId):
    name = "lava-docker-test-shell"
    description = "Runs lava-test-shell in a docker container"
    summary = "Runs lava-test-shell in a docker container"

    def __init__(self, wait_for_device=True):
        super().__init__()
        self.wait_for_device = wait_for_device

    def run(self, connection, max_end_time):
        # obtain lava overlay
        # start container
        # create USB device mapping to container
        # connect to container, and run lava-test-shell over it
        location = self.get_namespace_data(
            action="test", label="shared", key="location"
        )
        overlay = self.get_namespace_data(
            action="test", label="results", key="lava_test_results_dir"
        ).strip("/")

        image = self.parameters["docker"]["image"]
        container = "lava-docker-test-shell-%s-%s" % (self.job.job_id, self.level)

        board_id = self.get_board_id()
        device_info = {"board_id": board_id}
        add_device_container_mapping(
            job_id=self.job.job_id,
            device_info=device_info,
            container=container,
            container_type="docker",
            logging_info=self.get_logging_info(),
        )

        docker = DockerRun(image)
        docker.bind_mount(os.path.join(location, overlay), "/" + overlay)
        docker.interactive()
        docker.hostname("lava")
        docker.name(container)
        docker.environment("PS1", "docker-test-shell:$ ")
        if self.wait_for_device:
            devices = get_udev_devices(device_info=[device_info])
            for dev in devices:
                docker.add_device(dev)

        docker_cmd = docker.cmdline("bash", "--norc", "-i")

        cmd = " ".join([shlex.quote(s) for s in docker_cmd])
        self.logger.debug("Starting docker test shell container: %s" % cmd)
        shell = ShellCommand(cmd, self.timeout, logger=self.logger)

        shell_connection = ShellSession(self.job, shell)
        shell_connection.prompt_str = "docker-test-shell:"

        self.__set_connection__(shell_connection)
        super().run(shell_connection, max_end_time)

        # finish the container
        shell_connection.finalise()

        # return the original connection untouched
        self.__set_connection__(connection)
        return connection

    def __set_connection__(self, c):
        self.set_namespace_data(
            action="shared",
            label="shared",
            key="connection",
            value=c,
            parameters=self.parameters,
        )

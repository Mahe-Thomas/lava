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

import pytest
import re
import time
from pathlib import Path
from tests.lava_dispatcher.test_basic import Factory


from lava_common.compat import yaml_load
from lava_common.timeout import Timeout
from lava_dispatcher.device import NewDevice
from lava_dispatcher.job import Job
from lava_dispatcher.actions.test.docker import DockerTestAction


@pytest.fixture
def factory():
    return Factory()


@pytest.fixture
def job(factory):
    return factory.create_job(
        "hi6220-hikey-r2-01.jinja2", "sample_jobs/docker-test.yaml"
    )


@pytest.fixture
def action(job):
    return job.pipeline.actions[2]


@pytest.fixture
def first_test_action(action):
    return action


@pytest.fixture
def second_test_action(job):
    return job.pipeline.actions[3]


def test_validate_schema(factory):
    factory.validate_job_strict = True
    # The next call not crashing means that the strict schema validation
    # passed.
    factory.create_job("hi6220-hikey-r2-01.jinja2", "sample_jobs/docker-test.yaml")


def test_detect_correct_action(action):
    assert type(action).__name__ == "DockerTestAction"


def test_run(action, mocker):
    mocker.patch("lava_dispatcher.utils.fastboot.DockerDriver.__get_device_nodes__")
    ShellCommand = mocker.patch("lava_dispatcher.actions.test.docker.ShellCommand")
    ShellSesssion = mocker.patch("lava_dispatcher.actions.test.docker.ShellSession")
    docker_connection = mocker.MagicMock()
    ShellSesssion.return_value = docker_connection
    action_run = mocker.patch("lava_dispatcher.actions.test.docker.TestShellAction.run")
    connection = mocker.MagicMock()
    add_device_container_mapping = mocker.patch(
        "lava_dispatcher.actions.test.docker.add_device_container_mapping"
    )
    get_udev_devices = mocker.patch(
        "lava_dispatcher.actions.test.docker.get_udev_devices",
        return_value=["/dev/foobar"],
    )
    WaitDeviceBoardID_run = mocker.patch(
        "lava_dispatcher.utils.udev.WaitDeviceBoardID.run"
    )

    action.validate()
    action.run(connection, time.time() + 1000)

    # device is shared with the container
    add_device_container_mapping.assert_called_with(
        job_id=action.job.job_id,
        device_info={"board_id": "0123456789"},
        container=mocker.ANY,
        container_type="docker",
        logging_info=mocker.ANY,
    )

    WaitDeviceBoardID_run.assert_called()
    get_udev_devices.assert_called_with(device_info=[{"board_id": "0123456789"}])

    # overlay gets created
    overlay = next(Path(action.job.tmp_dir).glob("lava-create-overlay-*/lava-*"))
    assert overlay.exists()
    # overlay gets the correct content
    lava_test_runner = overlay / "bin" / "lava-test-runner"
    assert lava_test_runner.exists()
    lava_test_0 = overlay / "0"
    assert lava_test_runner.exists()

    environmentfile = overlay / "environment"
    environment = environmentfile.open().read()
    assert "ANDROID_SERIAL='0123456789'" in environment
    assert "LAVA_CONNECTION_COMMAND_UART0='telnet localhost 4002'" in environment
    assert "LAVA_CONNECTION_COMMAND_UART1='telnet 192.168.1.200 8001'" in environment
    # primary connection:
    assert "LAVA_CONNECTION_COMMAND='telnet 192.168.1.200 8001'" in environment

    # docker gets called
    docker_call = ShellCommand.mock_calls[0][1][0]
    assert docker_call.startswith("docker run")
    # overlay gets passed into docker
    assert (
        re.match(
            r".* --mount=type=bind,source=%s,destination=/%s" % (overlay, overlay.name),
            docker_call,
        )
        is not None
    )
    # device passed to docker
    assert "--device=/dev/foobar" in docker_call

    # the lava-test-shell implementation gets called with the docker shell
    action_run.assert_called_with(docker_connection, mocker.ANY)

    # the docker shell gets finalized
    docker_connection.finalise.assert_called()


def test_stages(first_test_action, second_test_action):
    assert first_test_action.parameters["stage"] == 0
    assert second_test_action.parameters["stage"] == 1


def test_wait_for_board_id_is_optional(factory):
    action = DockerTestAction()
    action.job = Job("1234", {}, None)
    rendered, _ = factory.create_device("hi6220-hikey-r2-01.jinja2")
    action.job.device = NewDevice(yaml_load(rendered))
    action.job.timeout = Timeout("blah")
    action.level = 1
    action.populate(
        {
            "namespace": "common",
            "docker": {"image": "foobar", "wait": {"device": False}},
        }
    )
    assert not any(
        [a for a in action.pipeline.actions if a.name == "wait-device-boardid"]
    )

    docker_test_shell = action.pipeline.actions[-2]
    assert not docker_test_shell.wait_for_device

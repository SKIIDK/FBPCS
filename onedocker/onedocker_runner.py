#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.


"""
CLI for running an executable in one docker
Usage:
    onedocker-runner <package_name> --cmd=<cmd> [options]
Options:
    -h --help                           Show this help
    --repository_path=<repository_path> The folder repository that the executables are to downloaded from
    --exe_path=<exe_path>               The folder that the executables are located at
    --timeout=<timeout>                 Set timeout (in sec) to task to avoid endless running
    --log_path=<path>                   Override the default path where logs are saved
    --verbose                           Set logging level to DEBUG
"""

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Tuple, Any, Optional

import psutil
from fbpcs.service.storage_s3 import S3StorageService
from fbpcs.util.s3path import S3Path

from .env import ONEDOCKER_EXE_PATH, ONEDOCKER_REPOSITORY_PATH


# the folder on s3 that the executables are to downloaded from
DEFAULT_REPOSITORY_PATH = "https://one-docker-repository.s3.us-west-1.amazonaws.com/"

# the folder in the docker image that is going to host the executables
DEFAULT_EXE_FOLDER = "/root/one_docker/package/"


# The handler dealing signal SIGINT, which could be Ctrl + C from user's terminal
def handler(signum, frame):
    raise InterruptedError


def run(
    repository_path: str,
    exe_path: str,
    package_name: str,
    cmd: str,
    logger: logging.Logger,
    timeout: int,
) -> None:
    # download executable from s3
    if repository_path.upper() != "LOCAL":
        logger.info("Downloading executables ...")
        _download_executables(repository_path, package_name)
    else:
        logger.info("Local repository, skip download ...")

    # grant execute permission to the downloaded executable file
    team, exe_name = _parse_package_name(package_name)
    subprocess.run(f"chmod +x {exe_path}/{exe_name}", shell=True)

    # TODO update this line after proper change in fbcode/measurement/private_measurement/pcs/oss/fbpcs/service/onedocker.py to take
    # out the hard coded exe_path in cmd string
    if repository_path.upper() == "LOCAL":
        cmd = exe_path + cmd

    # run execution cmd
    logger.info(f"Running cmd: {cmd} ...")
    signal.signal(signal.SIGINT, handler)
    """
     If start_new_session is true the setsid() system call will be made in the
     child process prior to the execution of the subprocess, which makes sure
     every process in the same process group can be killed by OS if timeout occurs.
     note: setsid() will set the pgid to its pid.
    """
    with subprocess.Popen(cmd, shell=True, start_new_session=True) as proc:
        net_start: Any = psutil.net_io_counters()
        try:
            proc.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, InterruptedError) as e:
            proc.terminate()
            os.killpg(proc.pid, signal.SIGTERM)
            raise e

        return_code = proc.wait()
        net_end: Any = psutil.net_io_counters()
        logger.info(
            f"Net usage: {net_end.bytes_sent - net_start.bytes_sent} bytes sent, {net_end.bytes_recv - net_start.bytes_recv} bytes received"
        )
        if return_code != 0:
            logger.info(f"Subprocess returned non-zero return code: {return_code}")
            sys.exit(return_code)


def _download_executables(
    repository_path: str,
    package_name: str,
) -> None:
    s3_region = S3Path(repository_path).region
    team, exe_name = _parse_package_name(package_name)
    exe_local_path = DEFAULT_EXE_FOLDER + exe_name
    exe_s3_path = repository_path + package_name
    storage_svc = S3StorageService(s3_region)
    storage_svc.copy(exe_s3_path, exe_local_path)


def _parse_package_name(package_name: str) -> Tuple[str, str]:
    return package_name.split("/")[0], package_name.split("/")[1]


def _read_config(
    logger: logging.Logger,
    config_name: str,
    argument: Optional[str],
    env_var: str,
    default_val: str,
):
    if argument:
        logger.info(f"Read {config_name} from program arguments...")
        return argument

    if os.getenv(env_var):
        logger.info(f"Read {config_name} from environment variables...")
        return os.getenv(env_var)

    logger.info(f"Read {config_name} from default value...")
    return default_val


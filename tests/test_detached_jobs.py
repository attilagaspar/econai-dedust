"""The detached-job command builders shared by Train and Fine-tune.

Guards the fix for the stuck-"already running" bug: the .pid file survives on
the bind mount and container PIDs are reused, so liveness must be (pid file
exists) AND (kill -0) AND (/proc cmdline names our script), and the job must
remove its own pid file on exit."""
from app.server import (_job_running_cmd, _job_launch_cmd, _job_tail_cmd,
                        _job_still_running_cmd)

CN   = "dedust_train_abc123"
PID  = "/workspace/layout-model-training/logs/proj_ft.pid"
LOG  = "/workspace/layout-model-training/logs/proj_ft.log"
SH   = "/workspace/layout-model-training/scripts/proj_finetune_from_src.sh"


def test_launch_wrapper_removes_pid_file_on_exit():
    cmd = _job_launch_cmd(CN, SH, LOG, PID)
    assert f"rm -f {PID}" in cmd                      # self-cleaning pid file
    assert f"bash {SH} >{LOG} 2>&1; rm -f {PID}" in cmd   # removal AFTER the job
    assert f"echo $! >{PID}" in cmd
    assert cmd.startswith(f"docker start {CN}")


def test_launch_self_stops_container_by_default():
    # the job's last act touches the sentinel → the container's keep-alive
    # loop exits → GPU freed even when no browser stream survived to clean up
    cmd = _job_launch_cmd(CN, SH, LOG, PID)
    assert "touch /tmp/dedust_selfstop" in cmd
    assert cmd.index(f"rm -f {PID}") < cmd.index("touch /tmp/dedust_selfstop")


def test_launch_keep_container_skips_self_stop():
    cmd = _job_launch_cmd(CN, SH, LOG, PID, self_stop=False)
    assert "dedust_selfstop" not in cmd


def test_running_probe_verifies_cmdline_not_just_pid():
    cmd = _job_running_cmd(CN, PID, SH)
    assert "kill -0 $pid" in cmd
    # a reused PID must not count: the probe greps the script's basename
    assert "/proc/$pid/cmdline" in cmd
    assert "proj_finetune_from_src.sh" in cmd
    assert "|| echo STOPPED" in cmd                   # stopped container → STOPPED


def test_tail_loop_ends_when_pid_file_disappears():
    cmd = _job_tail_cmd(CN, LOG, PID, "[done]")
    assert f"while [ -f {PID} ] && kill -0 $pid" in cmd
    assert "[done]" in cmd


def test_still_running_requires_pid_file():
    cmd = _job_still_running_cmd(CN, PID)
    assert f"[ -f {PID} ]" in cmd
    assert "|| echo DONE" in cmd

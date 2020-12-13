"""
utils
"""
from __future__ import print_function, unicode_literals
import inspect
import warnings
import sys
import shlex
import subprocess
import os
import io
import platform
import traceback


DEBUG = bool(os.environ.get("SETUPTOOLS_SCM_DEBUG"))
IS_WINDOWS = platform.system() == "Windows"
PY2 = sys.version_info < (3,)
PY3 = sys.version_info > (3,)
string_types = (str,) if PY3 else (str, unicode)  # noqa


def no_git_env(env):
    # adapted from pre-commit
    # Too many bugs dealing with environment variables and GIT:
    # https://github.com/pre-commit/pre-commit/issues/300
    # In git 2.6.3 (maybe others), git exports GIT_WORK_TREE while running
    # pre-commit hooks
    # In git 1.9.1 (maybe others), git exports GIT_DIR and GIT_INDEX_FILE
    # while running pre-commit hooks in submodules.
    # GIT_DIR: Causes git clone to clone wrong thing
    # GIT_INDEX_FILE: Causes 'error invalid object ...' during commit
    for k, v in env.items():
        if k.startswith("GIT_"):
            trace(k, v)
    return {
        k: v
        for k, v in env.items()
        if not k.startswith("GIT_")
        or k in ("GIT_EXEC_PATH", "GIT_SSH", "GIT_SSH_COMMAND")
    }


def trace(
    key,
    var=None,
    *wrong_to_pass,
    indent=1,
    **kw,
):
    if DEBUG:
        assert not wrong_to_pass

        if var or kw:
            print("  " * (indent - 1), end="")
            print(key + ": ", end="")

        if not var:
            if kw:
                print()
        else:
            if getattr(var, "splitlines", None) is not None:
                print()
                for line in var.splitlines():
                    print("  " * indent, _ensure_str(line))
            else:
                if isinstance(var, dict):
                    print()
                    for k, v in var.items():
                        trace(k, v, indent=indent + 1)
                else:

                    print(var)
        for k, v in kw.items():
            trace(k, v, indent=indent + 1)

        sys.stdout.flush()


def trace_exception():
    if DEBUG:
        traceback.print_exc()


def _ensure_str(str_or_bytes):
    if isinstance(str_or_bytes, str):
        return str_or_bytes
    else:
        return str_or_bytes.decode("utf-8", "surrogateescape")


def ensure_stripped_str(str_or_bytes):
    return _ensure_str(str_or_bytes).strip()


def _always_strings(env_dict):
    """
    On Windows and Python 2, environment dictionaries must be strings
    and not unicode.
    """
    if IS_WINDOWS or PY2:
        env_dict.update((key, str(value)) for (key, value) in env_dict.items())
    return env_dict


def _popen_pipes(cmd, cwd):
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        env=_always_strings(
            dict(
                no_git_env(os.environ),
                # os.environ,
                # try to disable i18n
                LC_ALL="C",
                LANGUAGE="",
                HGPLAIN="1",
            )
        ),
    )


def do_ex(cmd, cwd="."):
    trace("cmd", cmd)
    if os.name == "posix" and not isinstance(cmd, (list, tuple)):
        cmd = shlex.split(cmd)

    p = _popen_pipes(cmd, cwd)
    out, err = p.communicate()
    trace("results", out=out, err=err, ret=p.returncode, indent=2)
    return ensure_stripped_str(out), ensure_stripped_str(err), p.returncode


def do(cmd, cwd="."):
    out, err, ret = do_ex(cmd, cwd)
    if ret and not DEBUG:
        print(err)
    return out


def data_from_mime(path):
    with io.open(path, encoding="utf-8") as fp:
        content = fp.read()
    trace("content", repr(content))
    # the complex conditions come from reading pseudo-mime-messages
    data = dict(x.split(": ", 1) for x in content.splitlines() if ": " in x)
    trace("data", data)
    return data


def function_has_arg(fn, argname):
    assert inspect.isfunction(fn)

    if PY2:
        argspec = inspect.getargspec(fn).args
    else:

        argspec = inspect.signature(fn).parameters

    return argname in argspec


def has_command(name, warn=True):
    try:
        p = _popen_pipes([name, "help"], ".")
    except OSError:
        trace_exception()
        res = False
    else:
        p.communicate()
        res = not p.returncode
    if not res and warn:
        warnings.warn("%r was not found" % name, category=RuntimeWarning)
    return res


def require_command(name):
    if not has_command(name, warn=False):
        raise EnvironmentError("%r was not found" % name)

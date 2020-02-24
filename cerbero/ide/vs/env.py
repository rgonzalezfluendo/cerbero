# cerbero - a multi-platform build system for Open Source software
# Copyright (C) 2016 Nirbheek Chauhan <nirbheek@centricular.com>
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Library General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Library General Public License for more details.
#
# You should have received a copy of the GNU Library General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

import os
import subprocess
from pathlib import Path
from functools import lru_cache

from cerbero.enums import Architecture
from cerbero.errors import FatalError

# We only support Visual Studio 2015 as of now
VCVARSALLS = {
    'vs14': (
        (
            r'Microsoft Visual Studio 14.0',
        ),
        r'VC\vcvarsall.bat'
    ),
    'vs15': (
        (
            r'Microsoft Visual Studio\2017\Community',
            r'Microsoft Visual Studio\2017\Professional',
            r'Microsoft Visual Studio\2017\Enterprise',
            r'Microsoft Visual Studio\2017\Preview',
        ),
        r'VC\Auxiliary\Build\vcvarsall.bat'
    ),
    'vs16': (
        (
            r'Microsoft Visual Studio\2019\Community',
            r'Microsoft Visual Studio\2019\Professional',
            r'Microsoft Visual Studio\2019\Enterprise',
            r'Microsoft Visual Studio\2019\BuildTools',
            r'Microsoft Visual Studio\2019\Preview',
        ),
        r'VC\Auxiliary\Build\vcvarsall.bat'
    ),
}

def get_program_files_dir():
    if 'PROGRAMFILES(X86)' in os.environ:
        # Windows 64-bit
        return Path(os.environ['PROGRAMFILES(X86)'])
    elif 'PROGRAMFILES' in os.environ:
        # Windows 32-bit
        return Path(os.environ['PROGRAMFILES'])
    raise FatalError('Could not find path to 32-bit Program Files directory')

def get_vs_year_version(vcver):
    if vcver == 'vs14':
        return '2015'
    if vcver == 'vs15':
        return '2017'
    if vcver == 'vs16':
        return '2019'
    raise RuntimeError('Unknown toolset value {!r}'.format(vcver))

def _get_custom_vs_install(vs_version, vs_install_path):
    path = Path(vs_install_path)
    if not path.is_dir():
        raise FatalError('vs_install_path {!r} is not a directory'.format(path))
    suffix = VCVARSALLS[vs_version][1]
    path = path / suffix
    if not path.is_file():
        raise FatalError('Can\'t find vcvarsall.bat inside vs_install_path {!r}'.format(path))
    return path.as_posix(), vs_version

def get_vcvarsall(vs_version, vs_install_path):
    known_vs_versions = sorted(VCVARSALLS.keys(), reverse=True)
    if vs_version:
        if vs_version not in VCVARSALLS:
            raise FatalError('Requested Visual Studio version {} is not one of: '
                             '{}'.format(vs_version, ', '.join(known_vs_versions)))
    # Do we want to use a specific known Visual Studio installation?
    if vs_install_path:
        assert(vs_version)
        return _get_custom_vs_install(vs_version, vs_install_path)
    # Start searching.
    if vs_version:
        vs_versions = [vs_version]
    else:
        # If no specific version was requested, look for all known versions and
        # pick the newest one found.
        vs_versions = known_vs_versions
    program_files = get_program_files_dir()
    for vs_version in vs_versions:
        prefixes, suffix = VCVARSALLS[vs_version]
        for prefix in prefixes:
            path = program_files / prefix / suffix
            # Find the location of the Visual Studio installation
            if path.is_file():
                return path.as_posix(), vs_version
    raise FatalError('Microsoft Visual Studio not found. If you installed it, '
                     'please file a bug. We looked for: ' + ', '.join(versions))

def append_path(var, path, sep=';'):
    if var and not var.endswith(sep):
        var += sep
    if path and not path.endswith(sep):
        path += sep
    var += path
    return var

def get_vcvarsall_arg(arch, target_arch):
    if target_arch == Architecture.X86:
        # If arch is x86_64, this will cause the WOW64 version of MSVC to be
        # used, which is how most people compile 32-bit apps on 64-bit.
        return 'x86'
    if arch == Architecture.X86:
        if target_arch == Architecture.X86_64:
            return 'x86_amd64'
        elif target_arch == Architecture.ARM64:
            return 'x86_arm64'
        elif target_arch.is_arm():
            return 'x86_arm'
    elif arch == Architecture.X86_64:
        if target_arch == Architecture.X86_64:
            return 'amd64'
        elif target_arch == Architecture.ARM64:
            return 'amd64_arm64'
        elif Architecture.is_arm(target_arch):
            return 'amd64_arm'
    # FIXME: Does Visual Studio support arm/arm64 as build machines?
    elif arch == Architecture.ARM64 and target_arch == Architecture.ARM64:
        return 'arm64'
    elif Architecture.is_arm(arch) and Architecture.is_arm(target_arch):
            return 'arm'
    raise FatalError('Unsupported arch/target_arch: {0}/{1}'.format(arch, target_arch))

def run_and_get_env(cmd):
    env = os.environ.copy()
    env['VSCMD_ARG_no_logo'] = '1'
    env['VSCMD_DEBUG'] = ''
    output = subprocess.check_output(cmd, shell=True, env=env,
                                     universal_newlines=True)
    lines = []
    for line in output.split('\n'):
        if '=' in line:
            lines.append(line)
    return lines

# For a specific env var, get only the values that were prepended to it by MSVC
def get_envvar_msvc_values(msvc, nomsvc, sep=';'):
    index = msvc.index(nomsvc)
    return msvc[0:index]

@lru_cache()
def get_msvc_env(arch, target_arch, version=None, vs_install_path=None):
    ret_env = {}
    vcvarsall, vsver = get_vcvarsall(version, vs_install_path)

    without_msvc = run_and_get_env('set')
    arg = get_vcvarsall_arg(arch, target_arch)
    vcvars_env_cmd = '"{0}" {1} & {2}'.format(vcvarsall, arg, 'set')
    with_msvc = run_and_get_env(vcvars_env_cmd)

    msvc = dict([each.split('=', 1) for each in with_msvc])
    nomsvc = dict([each.split('=', 1) for each in without_msvc])

    for each in set(with_msvc) - set(without_msvc):
        var = each.split('=', 1)[0]
        # Use the new values for all vars, except PATH
        if var == 'PATH':
            ret_env[var] = get_envvar_msvc_values(msvc[var], nomsvc[var])
        else:
            ret_env[var] = msvc[var]
    return ret_env, vsver

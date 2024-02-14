# cerbero - a multi-platform build system for Open Source software
# Copyright (C) 2019, Fluendo, S.A.
#  Author: Pablo Marcos Oltra <pmarcos@fluendo.com>, Fluendo, S.A.
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
import tempfile
import urllib.parse
import aioftp
import logging
import urllib.parse

from cerbero.build.relocatabletar import *
from cerbero.config import Platform
from cerbero.errors import FatalError, RecipeNotFreezableError, EmptyPackageError, PackageNotFoundError
from cerbero.utils import N_, _, shell
from cerbero.utils.shell import Ftp
from cerbero.utils import messages as m
from cerbero.packages.disttarball import DistTarball
from cerbero.packages import PackageType
from netrc import netrc
from urllib.parse import urlparse

class BinaryRemote (object):
    """Interface for binary remotes"""

    async def binary_exists(self, package_name, remote_dir):
        '''
        Method to check if remote file exists
        @param package_name: Packages name to check
        @type package_name: str
        @param remote_dir: Remote directory to fetch from where packages exist
        @type remote_dir: str
        '''
        raise NotImplementedError

    async def fetch_binary(self, package_name, local_dir, remote_dir):
        '''
        Method to be overriden that fetches a binary

        @param package_name: Package to fetch
        @type package_name: str
        @param local_dir: Local directory to fetch to
        @type local_dir: str
        @param remote_dir: Remote directory to fetch from where packages exist
        @type remote_dir: str
        '''
        raise NotImplementedError

    def upload_binary(self, package_name, local_dir, remote_dir, env_file):
        '''
        Method to be overriden that uploads a binary

        @param package_name: Packages to upload
        @type package_name: str
        @param local_dir: Local directory to upload from
        @type local_dir: str
        @param remote_dir: Remote directory to upload to where packages exist
        @type remote_dir: str
        '''
        raise NotImplementedError


class FtpBinaryRemote (BinaryRemote):
    """FtpBinaryRemote is a simple and unsafe implementation"""

    def __init__(self, remote, username='', password=''):
        self.remote = 'ftp://' + remote
        self.username = username if username else 'anonymous'
        self.password = password

    def __str__(self):
        return 'remote \'{}\', username \'{}\', password \'{}\''.format(self.remote, self.username, self.password)

    @classmethod
    def from_netrc(cls, remote):
        user, _, password = netrc().authenticators(urlparse(remote).hostname)
        return cls(remote, user, password)

    async def binary_exists(self, package_name, remote_dir):
        exists = False
        remote = urllib.parse.urlparse(self.remote)
        port = 21 if not remote.port else remote.port
        logging.getLogger('aioftp.client').setLevel(logging.CRITICAL)
        async with aioftp.ClientSession(remote.hostname, port, self.username, self.password, socket_timeout=15) as ftp:
            exists = await ftp.exists(os.path.join(remote.path, remote_dir, package_name))
        return exists

    async def fetch_binary(self, package_name, local_dir, remote_dir):
        remote = urllib.parse.urlparse(self.remote)
        port = 21 if not remote.port else remote.port
        logging.getLogger('aioftp.client').setLevel(logging.CRITICAL)
        async with aioftp.ClientSession(remote.hostname, port, self.username, self.password, socket_timeout=15) as ftp:
            if package_name:
                local_filename = os.path.join(local_dir, package_name)
                local_sha256_filename = local_filename + '.sha256'
                download_needed = True
                remote_sha256_filename = os.path.join(remote.path, remote_dir, package_name) + '.sha256'
                local_sha256 = 'local_sha256'
                remote_sha256 = 'remote_sha256'

                await ftp.download(remote_sha256_filename, local_sha256_filename, write_into=True)
                # .sha256 file contains both the sha256 hash and the filename, separated by a whitespace
                with open(local_sha256_filename, 'r') as file:
                    remote_sha256 = file.read().split(' ')[0]

                try:
                    if os.path.isfile(local_filename):
                        local_sha256 = shell.file_sha256(local_filename).hex()
                        if local_sha256 == remote_sha256:
                            download_needed = False
                except Exception:
                    pass

                if download_needed:
                    try:
                        remote_file = os.path.join(remote.path, remote_dir, package_name)
                        if await ftp.exists(remote_file):
                            await ftp.download(remote_file, local_filename, write_into=True)
                            local_sha256 = shell.file_sha256(local_filename).hex()
                        else:
                            raise Exception
                    except Exception:
                        # Ensure there are no file leftovers
                        if os.path.exists(local_filename):
                            os.remove(local_filename)
                        if os.path.exists(local_sha256_filename):
                            os.remove(local_sha256_filename)
                        raise PackageNotFoundError(os.path.join(self.remote, remote_dir, package_name))

                    if remote_sha256 != local_sha256:
                        raise Exception('Local file \'{}\' hash \'{}\' is different than expected remote hash \'{}\''
                                        .format(remote_file, local_sha256, remote_sha256))

    def upload_binary(self, package_name, local_dir, remote_dir, env_file):
        with Ftp(self.remote, user=self.username, password=self.password) as ftp:
            remote = urllib.parse.urlparse(self.remote)
            remote_env_file = os.path.join(remote.path, remote_dir, os.path.basename(env_file))
            if not ftp.file_exists(remote_env_file):
                m.action('Uploading environment file to %s' % remote_env_file)
                ftp.upload(env_file, remote_env_file)
            if package_name:
                remote_filename = os.path.join(remote.path, remote_dir, package_name)
                remote_sha256_filename = remote_filename + '.sha256'
                local_filename = os.path.join(local_dir, package_name)
                local_sha256_filename = local_filename + '.sha256'
                upload_needed = True

                sha256 = shell.file_sha256(local_filename)
                # .sha256 file contains both the sha256 hash and the
                # filename, separated by a whitespace
                with open(local_sha256_filename, 'w') as f:
                    f.write('%s %s' % (sha256.hex(), package_name))

                try:
                    tmp_sha256 = tempfile.NamedTemporaryFile()
                    tmp_sha256_filename = tmp_sha256.name
                    ftp.download(remote_sha256_filename,
                                    tmp_sha256_filename)
                    with open(local_sha256_filename, 'r') as file:
                        local_sha256 = file.read().split()[0]
                    with open(tmp_sha256_filename, 'r') as file:
                        remote_sha256 = file.read().split()[0]
                    if local_sha256 == remote_sha256:
                        upload_needed = False
                except Exception:
                    pass

                if upload_needed:
                    ftp.upload(local_sha256_filename, remote_sha256_filename)
                    ftp.upload(local_filename, remote_filename)
                else:
                    m.action('No need to upload since local and remote SHA256 are the same for filename: {}'.format(package_name))


class Fridge (object):
    """This fridge packages recipes thar are already built"""

    # Freeze/Unfreeze steps
    FETCH_BINARY = (N_('Fetch Binary'), 'fetch_binary')
    EXTRACT_BINARY = (N_('Extract Binary'), 'extract_binary')
    GEN_BINARY = (N_('Generate Binary'), 'generate_binary')
    UPLOAD_BINARY = (N_('Upload Binary'), 'upload_binary')

    def __init__(self, store, dry_run=False):
        self.store = store
        self.cookbook = store.cookbook
        self.config = self.cookbook.get_config()
        self.binaries_remote = self.config.binaries_remote
        self.env_checksum = None
        shell.DRY_RUN = dry_run
        if not self.config.binaries_local:
            raise FatalError(_('Configuration without binaries local dir'))

    async def check_remote_binary_exists(self, recipe):
        self._ensure_ready(recipe)

        try:
            # Ensure the built_version is collected asynchronously before
            # calling _get_package_name, because that is done in a sync way and
            # would call otherwise the sync built_version, which takes time.
            # Since the built_version is cached, we can gather it here and will be
            # reused by both the sync and async flavors of built_version
            if hasattr(recipe, 'async_built_version'):
                await recipe.async_built_version()
            package_name = self._get_package_name(recipe)
            if await self.binaries_remote.binary_exists(package_name, self.env_checksum):
                m.action('{}: Remote package \'{}/{}\' found'.format(recipe, self.env_checksum, package_name))
            else:
                raise PackageNotFoundError(os.path.join(self.env_checksum, package_name))
        except PackageNotFoundError:
            raise
        except Exception as e:
            m.warning('Error checking if recipe %s exists in remote: %s' % (recipe.name, e))
            raise PackageNotFoundError(os.path.join(self.env_checksum, package_name))

    async def fetch_binary(self, recipe):
        self._ensure_ready(recipe)
        package_name = self._get_package_name(recipe)
        m.action('Downloading fridge binary {}/{}'.format(self.env_checksum, package_name))
        await self.binaries_remote.fetch_binary(package_name,
                                          self.binaries_local, self.env_checksum)

    def extract_binary(self, recipe):
        self._ensure_ready(recipe)
        package_name = self._get_package_name(recipe)
        # There is a weird bug where the links in the devel package are overwriting the
        # file it's linking instead of just creating the link.
        # For example libmonosgen-2.0.dylib will be extracted creating a link
        # libmonosgen-2.0.dylib -> libmonosgen-2.0.1.dylib and copying
        # libmonosgen-2.0.dylib to libmonosgen-2.0.1.dylib
        # As a workaround we extract first the devel package and finally the runtime
        if package_name:
            if self.config.target_platform == Platform.DARWIN:
                tarclass = RelocatableTarOSX
            else:
                tarclass = RelocatableTar
            tar = tarclass.open(os.path.join(self.binaries_local,
                                                package_name), 'r:bz2')
            tar.extract_and_relocate(self.config.prefix, self.config.toolchain_prefix)
            tar.close()

    def generate_binary(self, recipe):
        self._ensure_ready(recipe)
        p = self.store.get_package('%s-pkg' % recipe.name)
        tar = DistTarball(self.config, p, self.store)
        p.pre_package()
        files = self.cookbook.recipe_installed_files(recipe.name)
        if not files:
            m.warning('The recipe %s has no installed files. Try running all steps for the recipe from scratch' % recipe.name)
            raise EmptyPackageError(p.name)

        # Update list of installed files to make sure all of the files actually exist
        installed_files = self.cookbook.update_installed_files(recipe.name, files)
        paths = tar.pack_files(self.binaries_local, PackageType.DEVEL, installed_files)
        p.post_package(paths, self.binaries_local)

    def upload_binary(self, recipe):
        self._ensure_ready(recipe)
        package_name = self._get_package_name(recipe)
        fetch_package = None
        if os.path.exists(os.path.join(self.binaries_local, package_name)):
            fetch_package = package_name
        else:
            m.warning("No package was created for %s" % package_name)
        m.action('Uploading fridge binary {}/{}'.format(self.env_checksum, package_name))
        self.binaries_remote.upload_binary(fetch_package, self.binaries_local,
                                           self.env_checksum, self.env_file)

    def _ensure_ready(self, recipe):
        if not recipe.allow_package_creation:
            raise RecipeNotFreezableError(recipe.name)
        if not self.env_checksum:
            self.env_checksum = self.config.get_checksum()[:8]
            self.binaries_local = os.path.join(self.config.binaries_local, self.env_checksum)
            if not self.binaries_remote:
                raise FatalError(_('Configuration without binaries remote'))
            if not os.path.exists(self.binaries_local):
                os.makedirs(self.binaries_local)
            self.env_file = os.path.join(self.binaries_local, 'ENVIRONMENT')
            if not os.path.exists(self.env_file):
                with open(self.env_file, 'w') as f:
                    f.write('%s\n\n%s' % (self.env_checksum, self.config.get_string_for_checksum()))
            m.message('Fridge initialized with environment hash {}'.format(self.env_checksum))

    def _get_package_name(self, recipe):
        p = self.store.get_package('%s-pkg' % recipe.name)
        tar = DistTarball(self.config, p, self.store)
        return tar.get_name(PackageType.DEVEL)

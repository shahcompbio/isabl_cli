"""Data import logic."""

from collections import defaultdict
from datetime import datetime
from getpass import getuser
from os.path import basename
from os.path import getsize
from os.path import isdir
from os.path import join
import os
import re
import shutil
import subprocess

from slugify import slugify
import click
import yaml

from cli import api
from cli import options
from cli.settings import system_settings
from cli import utils


def symlink_workflow_to_projects(workflow):
    """Create symlink from workflow directory and projects directories."""
    for i in workflow['projects']:
        if not i['storage_url']:
            i = update_storage_url('projects', i['pk'])

        utils.force_symlink(
            workflow['storage_url'],
            join(i['storage_url'], workflow['system_id']))


def symlink_analysis_to_targets(analysis):
    """Create symlink from workflow directory and projects directories."""
    src = analysis['storage_url']
    dst = '__'.join([
        analysis['pipeline']['name'].lower().replace(' ', '_'),
        analysis['pipeline']['version'].lower().replace(' ', '_'),
        str(analysis['pk'])])

    for i in analysis['targets']:
        if not i['storage_url']:
            i = update_storage_url('workflows', i['pk'])
        utils.force_symlink(src, join(i['storage_url'], dst))

    if analysis['project_level_analysis']:
        i = analysis['project_level_analysis']
        if not i['storage_url']:
            i = update_storage_url('projects', i['pk'])
        utils.force_symlink(src, join(i['storage_url'], dst))


def trash_analysis_storage(analysis):
    """Move analysis `storage_url` to a trash directory."""
    if analysis['status'] == 'SUCCEEDED':
        raise click.UsageError("You can't wipe a succeeded analysis")

    if isdir(analysis['storage_url']):
        slug = f'primary_key_{analysis["pk"]}__user_{getuser()}__date_'
        slug += datetime.now(system_settings.TIME_ZONE).isoformat()

        trash_dir = system_settings.MAKE_STORAGE_DIRECTORY(
            base='.analyses_trash',
            identifier=analysis["pk"],
            use_hash=True)

        dst = join(trash_dir, slug)
        click.echo(f"\ntrashing: {analysis['storage_url']} -> {dst}\n")
        shutil.move(analysis['storage_url'], dst)


def make_storage_directory(
        base,
        identifier,
        use_hash=False,
        root=system_settings.BASE_STORAGE_DIRECTORY):
    """
    Get and create path to a data directory.

    The path is set to:

        <root>/<base>/<identifier>

    If `use_hash`, identifier must be integer and path is build using the
    four last digits of the identifier. Say identifiers is 12345, then path is:

        <root>/<base>/23/45/12345

    Arguments:
        base (str): instance's API base.
        identifier (str): instance's primary key.
        root (str): default is system_settings.BASE_STORAGE_DIRECTORY.
        use_hash (bool): hash integer identifier for directories.

    Returns:
        str: path to instance's data directory.
    """
    if not root:  # pragma: no cover
        raise click.UsageError('Setting `BASE_STORAGE_DIRECTORY` not defined.')

    if use_hash:
        if not str(identifier).isdigit():  # pragma: no cover
            raise click.UsageError('`use_hash` only supported for integers.')

        hash_1 = f'{identifier:04d}'[-4:-2]
        hash_2 = f'{identifier:04d}'[-2:]
        path = join(base, hash_1, hash_2)
    else:
        path = join(base)

    storage_directory = join(root, path, str(identifier))
    os.makedirs(storage_directory, exist_ok=True)
    return storage_directory


def update_storage_url(endpoint, identifier, use_hash=False, **data):
    """Make storage directory and return patched instance."""
    get_dir = system_settings.MAKE_STORAGE_DIRECTORY
    data['storage_url'] = get_dir(endpoint, identifier, use_hash=use_hash)
    return api.patch_instance(endpoint, identifier, **data)


class BaseImporter():

    @staticmethod
    def symlink(src, dst):
        """Create symlink from `src` to `dst`."""
        return utils.force_symlink(os.path.realpath(src), dst)

    @staticmethod
    def move(src, dst):
        """Rename `src` to `dst`."""
        return os.rename(os.path.realpath(src), dst)


class ReferenceDataImporter(BaseImporter):

    """An import engine for assemblies' `reference_data`."""

    @classmethod
    def import_data(cls, assembly, data_src, data_id, symlink, description):
        """
        Register input_bed_path in technique's storage dir and update `data`.

        Arguments:
            assembly (str): name of assembly.
            data_src (str): path to reference data.
            data_id (str): identifier that will be used for reference data.
            symlink (str): symlink instead of move.
            description (str): reference data description.

        Returns:
            dict: updated assembly instance as retrieved from API.
        """
        utils.check_admin()
        assembly = api.create_instance('assemblies', name=assembly)
        data_id = slugify(data_id, separator='_')
        click.echo(f'`data_id` set to: {click.style(data_id, fg="green")}')

        if data_id in assembly['reference_data']:
            raise click.UsageError(
                f"Assembly '{assembly['name']}' "
                f"has already reference data registered with id '{data_id}':\n"
                f'\n\t{assembly["reference_data"][data_id]}')

        if not assembly['storage_url']:
            assembly = update_storage_url('assemblies', assembly['name'])

        data_dir = join(assembly['storage_url'], data_id)
        data_dst = join(data_dir, basename(data_src))
        os.makedirs(data_dir, exist_ok=True)

        if symlink:
            click.echo(f'\nLinking:\n\t{data_src}\n\tto {data_dst}')
            cls.symlink(data_src, data_dst)
        else:
            click.echo(f'\nMoving:\n\t{data_src}\n\tto {data_dst}')
            cls.move(data_src, data_dst)

        click.secho(f'\nSuccess! patching {assembly["name"]}...', fg='green')
        assembly['reference_data'][data_id] = {}
        assembly['reference_data'][data_id]['url'] = data_dst
        assembly['reference_data'][data_id]['description'] = description

        return api.patch_instance(
            endpoint='assemblies',
            identifier=assembly['pk'],
            storage_usage=utils.get_tree_size(assembly['storage_url']),
            reference_data=assembly['reference_data'])

    @classmethod
    def as_cli_command(cls):
        """Get bed importer as click command line interface."""
        @click.command(name='import_reference_data')
        @click.option('--assembly', help='name of reference genome')
        @click.option('--description', help='reference data description')
        @click.option('--data-id', help='data identifier (will be slugified)')
        @options.REFERENCE_DATA_SOURCE
        @options.SYMLINK
        def cmd(assembly, data_id, symlink, description, data_src):
            """
            Register reference data in assembly's data directory.

            Incoming data (files or directories) will moved unless `--symlink`
            is provided.

            Assembly's `storage_url`, `storage_usage` and `reference_data`
            fields are updated, setting the latter to:

                'reference_data': {
                    <data identifier>: {
                        'url': path/to/targets_bedfile.bed,
                        'description': path/to/baits_bedfile.bed
                        },
                    ...
                    }
            """
            cls().import_data(
                data_id=data_id,
                symlink=symlink,
                data_src=data_src,
                assembly=assembly,
                description=description)

        return cmd


class BedImporter():

    """An import engine for techniques' bedfiles."""

    @staticmethod
    def process_bedfile(path):
        """Sort, tabix and gzip a bedfile."""
        command = ['sort', '-k1,1V', '-k2,2n', path]
        sorted_bed = subprocess.check_output(command)

        with open(path, '+w') as f:
            f.write(sorted_bed.decode('utf-8'))

        subprocess.check_call(['bgzip', path])
        subprocess.check_call(['tabix', '-p', 'bed', path + '.gz'])

        with open(path, '+w') as f:  # write uncompressed file again
            f.write(sorted_bed.decode('utf-8'))

    @classmethod
    def import_bedfiles(
            cls, technique_key, targets_path, baits_path,
            assembly, description=None):
        """
        Register input_bed_path in technique's storage dir and update `data`.

        Arguments:
            technique_key (int): technique primary key.
            targets_path (str): path to targets bedfile.
            baits_path (str): path to baits bedfile.
            assembly (str): name of reference genome for bedfile.
            description (str): a description of the bedfiles.

        Returns:
            dict: updated technique instance as retrieved from API.
        """
        utils.check_admin()
        technique = api.get_instance('techniques', technique_key)

        if assembly in technique['bedfiles']:
            raise click.UsageError(
                f"Technique '{technique['slug']}' "
                f"has registered bedfiles for '{assembly}':\n"
                f'\n\t{technique["bedfiles"][assembly]["targets"]}'
                f'\n\t{technique["bedfiles"][assembly]["baits"]}')

        if not technique['storage_url']:
            technique = update_storage_url('techniques', technique['pk'])

        api.create_instance('assemblies', name=assembly)
        beds_dir = join(technique['storage_url'], 'bedfiles', assembly)
        base_name = f'{technique["slug"]}.{assembly}'
        targets_dst = join(beds_dir, f'{base_name}.targets.bed')
        baits_dst = join(beds_dir, f'{base_name}.baits.bed')
        os.makedirs(beds_dir, exist_ok=True)

        for src, dst in [(targets_path, targets_dst), (baits_path, baits_dst)]:
            click.echo(f'\nCopying:\n\t{src}\n\tto {dst}')
            shutil.copy(src, dst)
            click.secho(f'\nProcessing {basename(dst)}...', fg='blue')
            cls.process_bedfile(dst)

        click.secho(f'\nSuccess! patching {technique["slug"]}...', fg='green')
        technique['bedfiles'][assembly] = {}
        technique['bedfiles'][assembly]['targets'] = targets_dst
        technique['bedfiles'][assembly]['baits'] = baits_dst
        technique['bedfiles'][assembly]['description'] = description

        return api.patch_instance(
            endpoint='techniques',
            identifier=technique['pk'],
            storage_usage=utils.get_tree_size(technique['storage_url']),
            bedfiles=technique['bedfiles'])

    @classmethod
    def as_cli_command(cls):
        """Get bed importer as click command line interface."""
        @click.command(name='import_bedfiles')
        @options.TECHNIQUE_PRIMARY_KEY
        @options.TARGETS_PATH
        @options.BAITS_PATH
        @click.option('--assembly', help='name of reference genome')
        @click.option('--description', help='bedfiles description')
        def cmd(key, assembly, targets_path, baits_path, description):
            """
            Register targets and baits bedfiles in technique's data directory.

            Incoming bedfiles will be compressed and tabixed.
            Both gzipped and uncompressed versions are kept.

            Instance's `storage_url`, `storage_usage` and `bedfiles` fields
            are updated, setting the latter to:

                'bedfiles': {
                    <assembly name>: {
                        'targets': path/to/targets_bedfile.bed,
                        'baits': path/to/baits_bedfile.bed
                        },
                    ...
                    }
            """
            cls().import_bedfiles(
                technique_key=key,
                targets_path=targets_path,
                baits_path=baits_path,
                assembly=assembly,
                description=description)

        return cmd


class DataImporter(BaseImporter):

    """
    A Data import engine for workflows.

    Attributes:
        FASTQ_REGEX (str): a regex pattern used to match fastq files.
        BAM_REGEX (str): a regex pattern to match bams.
        CRAM_REGEX (str): a regex pattern to match crams.
    """

    BAM_REGEX = r'\.bam$'
    CRAM_REGEX = r'\.cram$'
    FASTQ_REGEX = r'(([_.]R{0}[_.].+)|([_.]R{0}\.)|(_{0}\.))f(ast)?q(\.gz)?$'

    def import_data(
            self, directories, symlink=False, commit=False,
            key=lambda x: x['system_id'], files_data=None, **filters):
        """
        Import raw data for multiple workflows.

        Workflows's `storage_url`, `storage_usage`, `sequencing_data` are
        updated.

        Arguments:
            directories (list): list of directories to be recursively explored.
            symlink (bool): if True symlink instead of moving.
            commit (bool): if True perform import operation.
            key (function): given a workflow dict returns id to match.
            filters (dict): key value pairs to use as API query params.
            files_data (dict): keys are files basenames and values are
                dicts with extra annotations such as PL, LB, or any other.

        Raises:
            click.UsageError: if `key` returns the same identifier for multiple
                workflows. If a workflow matches both fastq and bam files.
                if cant determine read 1 or read 2 from matched fastq files.

        Returns:
            tuple: list of workflows for which data has been matched and a
                summary of the operation.
        """
        utils.check_admin()
        files_data = files_data or {}
        workflows_matched = []
        cache = defaultdict(dict)
        patterns = []
        identifiers = {}

        # validate files_data
        for i, j in files_data.items():
            if not isinstance(j, dict):  # pragma: no cover
                raise click.UsageError(
                    f'Invalid file data, expected dict {i}: {j}')

        # get workflows and load cache dictionary
        for i in api.get_instances('workflows', verbose=True, **filters):
            index = f"primary_key_{i['pk']}"
            using_id = f"{i['system_id']} (Skipped, identifier is NULL)"
            identifier = key(i)

            if identifier in identifiers:  # duplicated identifiers not valid
                raise click.UsageError(
                    f"Can't use same identifier for {i['system_id']} "
                    f'and {identifiers[identifier]}: {identifier}')

            if identifier and not i['sequencing_data']:
                identifiers[identifier] = i['system_id']
                patterns.append(self.get_regex_pattern(index, identifier))
                using_id = f"{i['system_id']} (using {identifier})"

            cache[index]['using_id'] = using_id
            cache[index]['instance'] = i
            cache[index]['files'] = []

        if patterns:
            # see http://stackoverflow.com/questions/8888567 for pattern
            pattern = re.compile('|'.join(patterns))
            data_storage_dir = system_settings.BASE_STORAGE_DIRECTORY
            label = f'Exploring directories...'

            # explore dirs
            for directory in directories:
                with click.progressbar(os.walk(directory), label=label) as bar:
                    for root, _, files in bar:
                        if not root.startswith(data_storage_dir):
                            for i in files:
                                path = join(root, i)
                                index = self.match_path(path, pattern)
                                if index:
                                    cache[index]['files'].append(path)

            # process files if needed
            label = 'Processing...'
            bar = sorted(cache.values(), key=lambda x: x['instance']['pk'])
            with click.progressbar(bar, label=label) as bar:
                for i in bar:
                    if commit and i['files']:
                        workflows_matched.append(self.import_files(
                            instance=i['instance'],
                            files=i['files'],
                            symlink=symlink,
                            files_data=files_data))
                    elif i['files']:  # pragma: no cover
                        workflows_matched.append(i['instance'])

        return workflows_matched, self.get_summary(cache)

    def match_path(self, path, pattern):
        """Match `path` with `pattern` and update cache if fastq or bam."""
        try:
            matches = pattern.finditer(path)
            index = next(matches).lastgroup
            assert index is not None  # happens when pattern is empty

            # check if valid data type
            valid = True if re.search(self.BAM_REGEX, path) else False
            valid |= True if re.search(self.CRAM_REGEX, path) else False

            for i in [1, 2]:
                if re.search(self.FASTQ_REGEX.format(i), path):
                    valid = True

            if re.search(r'\.f(ast)?q(\.gz)?$', path) and not valid:
                msg = f'cant determine if read 1 or read 2 from: {path}'
                raise click.UsageError(msg)

            assert valid
        except (StopIteration, AssertionError):  # pragma: no cover
            return None

        return index

    def import_files(self, instance, files, files_data, symlink):
        """
        Move/link files into instance's `storage_url` and update database.

        Arguments:
            instance (dict): workflow instance.
            files (dict): list of files to be imported.
            symlink (dict): whether to symlink or move the data.
            files_data (dict): keys are files basenames and values are
                dicts with extra annotations such as PL, LB, or any other.

        Raises:
            click.UsageError: if multiple data formats are found.

        Returns:
            dict: patched workflow instance.
        """
        sequencing_data = []
        src_dst = []

        if not instance['storage_url']:
            instance = update_storage_url(
                endpoint='workflows',
                identifier=instance['pk'],
                use_hash=True)

        data_dir = join(instance['storage_url'], 'data')
        os.makedirs(data_dir, exist_ok=True)

        for src in files:
            file_name = basename(src)
            file_data = files_data.get(file_name, {})

            if re.search(self.BAM_REGEX, src):
                file_type = 'BAM'
            elif re.search(self.CRAM_REGEX, src):
                file_type = 'CRAM'
            else:
                file_type = 'FASTQ'
                file_name = self.format_fastq_name(file_name)

            if not file_name.startswith(instance['system_id']):
                file_name = f'{instance["system_id"]}__{file_name}'

            dst = join(data_dir, file_name)
            src_dst.append((src, dst))
            sequencing_data.append(dict(
                file_url=dst,
                file_type=file_type,
                file_data=file_data,
                hash_value=getsize(src),
                hash_method="os.path.getsize"))

        if len({i['file_type'] for i in sequencing_data}) > 1:
            raise click.UsageError(
                'We should have catched this earlier, but multiple formats are '
                f'not supported, these were found for {instance["system_id"]}: '
                f'{",".join(i["file_url"] for i in sequencing_data)}')

        for src, dst in src_dst:
            if symlink:
                self.symlink(src, dst)
            else:
                self.move(src, dst)

        return api.patch_instance(
            endpoint='workflows',
            identifier=instance['pk'],
            storage_url=instance['storage_url'],
            storage_usage=utils.get_tree_size(instance['storage_url']),
            sequencing_data=sequencing_data)

    def format_fastq_name(self, file_name):
        """Return destination file name."""
        suffix = None

        for i in [1, 2]:
            if re.search(self.FASTQ_REGEX.format(i), file_name):
                suffix = f'_{system_settings.FASTQ_READ_PREFIX}{i}.fastq'
                break

        assert suffix, f"Couldn't determine read 1 or read 2 from {file_name}"
        letter_index_fastq = r'[_.]R{}([_.])?\.f(ast)?q'.format(i)
        number_index_fastq = r'[_.]{}([_.])?\.f(ast)?q'.format(i)
        letter_index_any_location = r'[_.]R{}[_.]'.format(i)
        file_name = re.sub(letter_index_fastq, '.fastq', file_name)
        file_name = re.sub(number_index_fastq, '.fastq', file_name)
        file_name = re.sub(letter_index_any_location, '_', file_name)
        return re.sub(r'[_.]f(ast)?q', suffix, file_name)

    @staticmethod
    def get_regex_pattern(group_name, identifier):
        """
        Get regex pattern for `identifier` group as `group_name`.

        This pattern treats dashes, underscores and dots equally.

        Arguments:
            group_name (str): regex pattern group name.
            identifier (str): identifier to be matched by regex.

        Returns:
            str: a regex pattern.
        """
        pattern = re.sub(r'[-_.]', r'[-_.]', identifier)
        return r'(?P<{}>(^|[-_.])?{}[-_.])'.format(group_name, pattern)

    @staticmethod
    def get_summary(cache):
        """Get a summary of the matched, skipped, and missing files."""
        skipped, missing, matched, total_matched, nl = [], [], [], 0, '\n'

        for i in cache.values():
            if i['instance']['sequencing_data']:
                msg = click.style(f"skipped {i['using_id']}\t", fg='cyan')
                skipped.append(msg)
            elif i['files']:
                msg = click.style(f"found {i['using_id']}\n\t\t", fg='green')
                total_matched += len(i['files'])
                matched.append(msg + '\n\t\t'.join(i['files']))
            else:
                msg = click.style(f"missing {i['using_id']}\t", fg='red')
                missing.append(msg + 'no files matched')

        return (
            f"{nl.join([nl] + skipped) if skipped else ''}"
            f"{nl.join([nl] + missing) if missing else ''}"
            f"{nl.join([nl] + matched) if matched else ''}"
            f'\n\ntotal samples: {len(cache)}'
            f'\nsamples skipped: {len(skipped)}'
            f'\nsamples missing: {len(missing)}'
            f'\nsamples matched: {len(matched)}'
            f'\ntotal files matched: {total_matched}')

    @classmethod
    def as_cli_command(cls):
        """Get data importer as a click command line interface."""
        @click.command(name='import_data')
        @options.DIRECTORIES
        @options.IDENTIFIER
        @options.FILTERS
        @options.COMMIT
        @options.SYMLINK
        @options.FILES_DATA
        def cmd(identifier, commit, filters, directories, symlink, files_data):
            """
            Find and import data for multiple workflows from many directories.

            Search is recursive and any cram, bam or fastq file that matches
            the workflow identifier will be imported. Multiple data types for
            same workflow is not currently supported.

            Its possible to provide custom annotation per file (e.g. PL, PU, or
            LB in the case of fastq data). In order to do so, provide a yaml
            file using the `--files-data` argument. Such file must look
            like this, please note that keys are file names, not full paths:

            \b
                1.fq:
                    ID: 9
                    LB: Library_id
                    PL: ILLUMINA
                    PM: HiSeq-XTen
                    PU: MICHELLE
                2.fq:
                    LB: Library2
                    ...
            """
            def key(workflow):
                value, types = workflow, (int, str, type(None))
                for i in identifier:
                    value = value.get(i)
                if not isinstance(value, types):
                    raise click.UsageError(
                        f'invalid type for identifier '
                        f'`{".".join(identifier)}`: {type(value)}')
                return value

            if files_data:
                with open(files_data) as f:
                    files_data = yaml.load(f.read())
            else:
                files_data = {}

            matched, summary = cls().import_data(
                directories=directories, symlink=symlink, commit=commit,
                key=key, files_data=files_data, **filters)

            click.echo(summary)

            if not commit and matched:  # pragma: no cover
                utils.echo_add_commit_message()

        return cmd

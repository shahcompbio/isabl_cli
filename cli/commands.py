"""commands logic."""

from glob import glob
from os.path import join
import shutil
import subprocess

import click

from cli import api
from cli import options
from cli import utils
from cli.settings import system_settings
from cli.settings import import_from_string


@click.command()
@click.option('--project', help='primary key of project to merge by', type=int)
@click.option('--pipeline', help='analyses pipeline primary key', type=int)
def merge_analyses(project, pipeline):  # pragma: no cover
    """Merge analyses by project primary key."""
    project = api.get_instance('projects', project)
    pipeline = api.get_instance('pipelines', pipeline)
    pipeline_class = import_from_string(pipeline['pipeline_class'])()
    pipeline_class.merge_project_analyses(project)


@click.command()
@options.FILTERS
def processed_finished(filters):
    """Process and update finished analyses."""
    utils.check_admin()
    filters.update(status='FINISHED')

    for i in api.get_instances('analyses', **filters):
        if i['ran_by'] != system_settings.api_username:  # admin must own dir
            src = i['storage_url'] + '__tmp'
            shutil.move(i['storage_url'], src)
            cmd = utils.get_rsync_command(src, i['storage_url'], chmod='a-w')
            subprocess.check_call(cmd, shell=True)

        api.patch_instance(
            endpoint='analyses',
            identifier=i['pk'],
            status='SUCCEEDED',
            storage_usage=utils.get_tree_size(i['storage_url']))


@click.command()
@options.ANALYSIS_PRIMARY_KEY
@options.ANALYSIS_STATUS
def patch_status(key, status):
    """Patch status of a given analysis."""
    analysis = api.get_instance('analyses', key)
    api.patch_instance(
        endpoint='analyses',
        identifier=analysis['pk'],
        status=status,
        storage_usage=utils.get_tree_size(analysis['storage_url']))


@click.command()
@options.ENDPOINT
@options.FIELDS
@options.NULLABLE_FILTERS
@options.NO_HEADERS
def get_attributes(endpoint, field, filters, no_headers):
    """Get database attributes from API."""
    result = [] if no_headers else ['\t'.join('.'.join(i) for i in field)]
    filters['fields'] = ','.join(i[0] for i in field)

    for i in api.get_instances(endpoint, verbose=True, **filters):
        values = [utils.traverse_dict(i, j, serialize=True) for j in field]
        result.append('\t'.join(values))

    click.echo('\n'.join(result).expandtabs(30))


@click.command()
@options.ENDPOINT
@options.NULLABLE_FILTERS
def get_count(endpoint, filters):
    """Get count of database instances."""
    click.echo(api.get_instances_count(endpoint, **filters))


@click.command()
@options.ENDPOINT
@options.FILE_PATTERN
@options.FILTERS
def get_paths(endpoint, pattern, filters):
    """Get storage directories, use `pattern` to match files inside dirs."""
    filters.update(fields='storage_url', limit=100000)
    for i in api.get_instances(endpoint, verbose=True, **filters):
        if i['storage_url']:
            if pattern:
                click.echo('\n'.join(glob(join(i['storage_url'], pattern))))
            else:
                click.echo(i['storage_url'])

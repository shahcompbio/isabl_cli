import os

import click
import pytest

from cli import _DEFAULTS
from cli import api
from cli import data

from . import factories

from click.testing import CliRunner

from cli import cli


def test_trash_analysis_storage():
    # the rest is tested in test_engine
    with pytest.raises(click.UsageError) as error:
        data.trash_analysis_storage({'status': 'SUCCEEDED'})
    assert "You can't wipe a succeeded analysis" in str(error.value)


def test_get_storage_directory():
    i = data.get_storage_directory('test', 12345, root='/', use_hash=True)
    j = data.get_storage_directory('test', 12345, root='/', use_hash=False)
    assert i == '/data/test/23/45/12345'
    assert j == '/data/test/12345'


def test_import_bed(tmpdir):
    data_storage_directory = tmpdir.mkdir('data_storage_directory')
    _DEFAULTS['BASE_STORAGE_DIRECTORY'] = str(data_storage_directory)
    technique = api.create_instance('techniques', **factories.TechniqueFactory())
    bed = tmpdir.join('test.bed')
    bed.write('2\t1\t2\n1\t1\t2\n')
    technique = data.import_bed(technique['pk'], bed.strpath)

    assert technique['bed_url']
    assert os.path.isfile(technique['bed_url'])
    assert os.path.isfile(technique['bed_url'] + '.gz')
    assert os.path.isfile(technique['bed_url'] + '.gz.tbi')

    with open(technique['bed_url'], 'r') as f:  # test bed is sorted
        assert next(f).startswith('1')

    with pytest.raises(click.UsageError) as error:
        data.import_bed(technique['pk'], bed.strpath)

    assert 'as a bed registered' in str(error.value)


def test_local_data_import(tmpdir):
    data_storage_directory = tmpdir.mkdir('data_storage_directory')
    _DEFAULTS['BASE_STORAGE_DIRECTORY'] = data_storage_directory.strpath
    keys = [1, 2, 3]
    workflows = api.get_instances('workflows', keys)

    if not workflows:
        workflows = [factories.WorkflowFactory() for i in range(3)]
        workflows = [api.create_instance('workflows', **i) for i in workflows]

    for i in workflows:
        api.patch_instance('workflows', i['pk'], data_type=None)

    importer = data.LocalDataImporter()
    importer.import_data(directories=[tmpdir.strpath], pk__in=keys)
    obtained = len(importer.get_summary().rsplit('no files matched'))
    assert obtained == 3 + 1

    with pytest.raises(click.UsageError) as error:
        path_1 = tmpdir.join(f'{workflows[0]["system_id"]}.fastq')
        path_1.write('foo')
        importer.import_data(directories=[tmpdir.strpath], pk__in=keys)

    path_1.remove()
    assert 'cant determine if read 1 or read 2' in str(error.value)

    path_1 = tmpdir.join(f'{workflows[0]["system_id"]}_R1_foo.fastq')
    path_2 = tmpdir.join(f'{workflows[0]["system_id"]}_R2_foo.fastq')
    path_1.write('foo')
    path_2.write('foo')

    importer.import_data(directories=[tmpdir.strpath], pk__in=keys, commit=True)
    assert 'samples matched: 1' in importer.get_summary()

    with pytest.raises(click.UsageError) as error:
        path_1 = tmpdir.join(f'{workflows[1]["system_id"]}_1.fastq')
        path_2 = tmpdir.join(f'{workflows[1]["system_id"]}.bam')
        path_1.write('foo')
        path_2.write('foo')
        importer.import_data(directories=[tmpdir.strpath], pk__in=keys)

    path_1.remove()
    path_2.remove()
    assert 'matched different data types' in str(error.value)

    with pytest.raises(click.UsageError) as error:
        api.patch_instance('workflows', workflows[1]['pk'], center_id='dup_id')
        api.patch_instance('workflows', workflows[2]['pk'], center_id='dup_id')
        importer.import_data(
            key=lambda x: x['center_id'],
            directories=[tmpdir.strpath],
            pk__in=keys)

    assert 'same identifier for' in str(error.value)

    path_1 = tmpdir.join(f'{workflows[1]["system_id"]}_R1_foo.fastq')
    path_2 = tmpdir.join(f'{workflows[1]["system_id"]}_R2_foo.fastq')
    path_3 = tmpdir.join(f'{workflows[2]["system_id"]}_bam1_.bam')
    path_4 = tmpdir.join(f'{workflows[2]["system_id"]}_bam2_.bam')

    path_1.write('foo')
    path_2.write('foo')
    path_3.write('foo')
    path_4.write('foo')

    imported = importer.import_data(
        directories=[tmpdir.strpath],
        commit=True,
        symlink=True,
        pk__in=keys)

    assert imported[0]['storage_usage'] > 0
    assert imported[0]['data_type'] == 'FASTQ'
    assert imported[1]['data_type'] == 'BAM'
    assert 'workflows' in imported[1]['storage_url']
    assert len(os.listdir(imported[1]['storage_url'])) == 2
    assert 'samples matched: 2' in importer.get_summary()
    assert 'samples skipped: 1' in importer.get_summary()

    command = data.LocalDataImporter.as_cli_command()
    runner = CliRunner()
    args = ['-di', tmpdir.strpath, '-id', 'system_id', '-fi', 'pk__in', keys]
    result = runner.invoke(command, args, catch_exceptions=False)
    assert 'samples skipped: 3' in result.output

    args = ['-di', tmpdir.strpath, '-id', 'specimen', '-fi', 'pk__in', keys]
    result = runner.invoke(command, args)
    assert 'invalid type for identifier' in result.output


def test_get_dst():
    importer = data.LocalDataImporter()
    assert importer.format_fastq_dst('not a fastq') is None
    assert importer.format_bam_dst('not a bam') is None

    bam_test = [
        'sample.bam',
        'sample.bam.bai',
        'sample.bam.md5',
        ]

    fastq_test = [
        ('sample_R{}_moretext', 'sample_moretext_{}'),
        ('sample_R{}_', 'sample_{}'),
        ('sample_R{}', 'sample_{}'),
        ('sample.R{}.more_text', 'sample_more_text_{}'),
        ('sample.R{}.', 'sample_{}'),
        ('sample.R{}', 'sample_{}'),
        ('sample_{}', 'sample_{}'),
        ]

    for i in bam_test:
        assert importer.format_bam_dst(i) == i

    for test, expected in fastq_test:
        for index in [1, 2]:
            for fastq in ['.fastq', '.fq']:
                for gzipped in ['', '.gz']:
                    path = test.format(index) + fastq + gzipped
                    obtained = importer.format_fastq_dst(path)
                    assert obtained == expected.format(index) + '.fastq' + gzipped

"""Isabl CLI utils."""

import getpass
import json
import os
import sys
import tarfile

import click

from isabl_cli.settings import system_settings


def get_results(
    experiment,
    application_key,
    result_key,
    targets=None,
    references=None,
    analyses=None,
    status="SUCCEEDED",
):
    """
    Match results from a experiment object.

    If targets, references or analyses are provided the analysis result must
    match these list of samples and dependencies.

    Pass `result_key='storage_url'` to get the output directory.

    Arguments:
        experiment (dict): experiment object for which result will be retrieved.
        application_key (int): key of the application that generated the result.
        result_key (dict): name of the result.
        targets (list): target experiments dicts that must match.
        references (dict): reference experiments dicts that must match.
        analyses (dict): analyses dicts that must match.
        status (str): expected analysis status.

    Returns:
        list: of tuples (result_value, analysis primary key).
    """
    results = []
    targets = {i.pk for i in targets or []}
    references = {i.pk for i in references or []}
    analyses = {i.pk for i in analyses or []}

    for i in experiment.results:
        if i.application.pk == application_key:
            if targets and {j.pk for j in i.targets}.difference(targets):
                continue

            if references and {j.pk for j in i.references}.difference(references):
                continue

            if analyses and not analyses.issubset({j.pk for j in i.analyses}):
                continue

            results_dict = i if result_key == "storage_url" else i.results
            result = results_dict.get(result_key)
            results.append((result, i.pk))

            assert result_key in results_dict, (
                f"Result '{result_key}' not found for analysis {i.pk}"
                f"({i.application.name} {i.application.version}) "
                f"with status: {i.status}"
            )

            assert i.status == status if status else True, (
                f"Expected status '{status}' for result '{result_key}' did not match: "
                f"{i.pk}({i.application.name} {i.application.version}) is {i.status}"
            )

    return results


def get_result(*args, application_name=None, **kwargs):
    """
    See get_results for full signature.

    Arguments:
        args (list): see get_results.
        kwargs (dict): see get_results.
        application_name (str): app name to display a more explicit error.

    Returns:
        tuple: result value, analysis pk that produced the result
    """
    app_name = application_name or kwargs.get("application_key")
    results = get_results(*args, **kwargs)
    assert results, f"No results found for application: {app_name}"
    assert len(results) == 1, f"Multiple results returned {results}"
    result, key = results[0]
    return result, key


def traverse_dict(dictionary, keys, serialize=False):
    """
    Traverse a `dictionary` using a list of `keys`.

    Arguments:
        dictionary (dict): dict to be traversed.
        keys (list): keys to be explored.
        serialize (bool): force to string, if value is dict use json.dumps.

    Returns:
        str: if `serialize` is True.
        object: if `serialize` is false.
    """
    value = dictionary

    for i in keys:
        try:
            if isinstance(value, list):
                value = [j.get(i, f"INVALID KEY ({i})") for j in value]
            else:
                value = value.get(i, f"INVALID KEY ({i})")
        except AttributeError:
            value = f"INVALID KEY ({i})"

    if serialize:
        if isinstance(value, dict):
            value = json.dumps(value)
        else:
            value = str(value)

    return value


def apply_decorators(decorators):
    """
    Apply a list of decorators to callable.

    See: http://stackoverflow.com/questions/4122815
    """
    decorators = reversed(decorators)

    def decorator(f):
        for i in decorators:
            f = i(f)
        return f

    return decorator


def get_rsync_command(src, dst, chmod="a-w"):
    """Get str for commant to move `src` directory to `dst`."""
    return (
        f"(chmod -R u+w {dst} || true) && "
        f"rsync -va --append-verify --remove-source-files {src}/ {dst}/ && "
        f"chmod -R {chmod} {dst} && "
        f"find {src}/ -depth -type d -empty "
        r'-exec rmdir "{}" \;'
    )


def get_tree_size(path, follow_symlinks=False):
    """Return total size of directory in bytes."""
    total = 0
    for entry in os.scandir(path):
        if entry.is_dir(follow_symlinks=follow_symlinks):
            total += get_tree_size(entry.path)
        else:
            total += entry.stat(follow_symlinks=follow_symlinks).st_size
    return total


def force_link(src, dst):
    """Force a link between src and dst."""
    try:
        os.unlink(dst)
        os.link(src, dst)
    except OSError:
        os.link(src, dst)


def force_symlink(src, dst):
    """Force a symlink between src and dst."""
    try:
        os.unlink(dst)
        os.symlink(src, dst)
    except OSError:
        os.symlink(src, dst)


def tar_dir(output_path, source_dir):
    """Compress a `source_dir` in `output_path`."""
    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))


def check_admin(msg=None):
    """Raise `PermissionError` if user is not `system_settings.ADMIN_USER`."""
    admin = system_settings.ADMIN_USER
    msg = msg or f"Operation can only be performed by {admin}"

    if getpass.getuser() != admin:
        raise PermissionError(msg)


def echo_add_commit_message():
    """Echo add `--commit` flag message."""
    click.secho("\nAdd --commit to proceed.\n", fg="green", blink=True)


def echo_title(title, color="cyan", blink=False):
    """Echo a title."""
    title = "\n" + title.strip().upper() + "\n"
    title += "".join("-" for i in title.strip()) + "\n"
    click.secho(title, fg=color, file=sys.stderr, blink=blink)

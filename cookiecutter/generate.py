#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
cookiecutter.generate
---------------------

Functions for generating a project from a project template.
"""
from __future__ import unicode_literals
import logging
import os
import shutil
import sys

from jinja2 import FileSystemLoader, Template
from jinja2.environment import Environment
from jinja2.exceptions import TemplateSyntaxError
from binaryornot.check import is_binary

from .exceptions import NonTemplatedInputDirException
from .find import find_template
from .utils import make_sure_path_exists, unicode_open, work_in
from .hooks import run_hook

import yaml

if sys.version_info[:2] < (2, 7):
    import simplejson as json
    from ordereddict import OrderedDict
else:
    import json
    from collections import OrderedDict


def ordered_load(stream, Loader=yaml.Loader, object_pairs_hook=OrderedDict):
    """
    http://stackoverflow.com/questions/5121931
    """
    class OrderedLoader(Loader):
        pass
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        lambda loader, node: object_pairs_hook(loader.construct_pairs(node)))
    return yaml.load(stream, OrderedLoader)


def generate_context(context_file=None, default_context=None):
    """
    Generates the context for a Cookiecutter project template.
    Loads the JSON file as a Python object, with key being the JSON filename.

    :param context_file: JSON file containing key/value pairs for populating
        the cookiecutter's variables.
    :param config_dict: Dict containing any config to take into account.
    """

    context = {}

    if context_file is None:
        context_file = 'cookiecutter.json'

    if not os.path.exists(context_file) and os.path.exists('cookiecutter.yml'):
        context_file = 'cookiecutter.yml'

    file_stem, file_ext = os.path.splitext(os.path.basename(context_file))

    with open(context_file) as file_handle:
        if file_ext == ".json":
            context = json.load(
                file_handle,
                encoding='utf-8',
                object_pairs_hook=OrderedDict)
        elif file_ext == ".yml":
            context = ordered_load(
                file_handle,
                yaml.SafeLoader,
                OrderedDict)

    # Overwrite context variable defaults with the default context from the
    # user's global config, if available
    if default_context:
        context.update(default_context)

    logging.debug('Context generated is {0}'.format(context))
    return context


def generate_file(project_dir, infile, context, env):
    """
    1. Render the filename of infile as the name of outfile.
    2. Deal with infile appropriately:

        a. If infile is a binary file, copy it over without rendering.
        b. If infile is a text file, render its contents and write the
           rendered infile to outfile.

    Precondition:

        When calling `generate_file()`, the root template dir must be the
        current working directory. Using `utils.work_in()` is the recommended
        way to perform this directory change.

    :param project_dir: Absolute path to the resulting generated project.
    :param infile: Input file to generate the file from. Relative to the root
        template dir.
    :param context: Dict for populating the cookiecutter's variables.
    :param env: Jinja2 template execution environment.
    """

    logging.debug("Generating file {0}".format(infile))

    # Render the path to the output file (not including the root project dir)
    outfile_tmpl = Template(infile)
    outfile = os.path.join(project_dir, outfile_tmpl.render(**context))
    logging.debug("outfile is {0}".format(outfile))

    # Just copy over binary files. Don't render.
    logging.debug("Check {0} to see if it's a binary".format(infile))
    if is_binary(infile):
        logging.debug("Copying binary {0} to {1} without rendering"
                      .format(infile, outfile))
        shutil.copyfile(infile, outfile)
    else:
        # Force fwd slashes on Windows for get_template
        # This is a by-design Jinja issue
        infile_fwd_slashes = infile.replace(os.path.sep, '/')

        # Render the file
        try:
            tmpl = env.get_template(infile_fwd_slashes)
        except TemplateSyntaxError as exception:
            # Disable translated so that printed exception contains verbose
            # information about syntax error location
            exception.translated = False
            raise
        rendered_file = tmpl.render(**context)

        logging.debug("Writing {0}".format(outfile))

        with unicode_open(outfile, 'w') as fh:
            fh.write(rendered_file)

    # Apply file permissions to output file
    shutil.copymode(infile, outfile)


def render_and_create_dir(dirname, context, output_dir):
    """
    Renders the name of a directory, creates the directory, and returns its
    path.
    """

    name_tmpl = Template(dirname)
    rendered_dirname = name_tmpl.render(**context)
    logging.debug('Rendered dir {0} must exist in output_dir {1}'.format(
        rendered_dirname,
        output_dir
    ))
    dir_to_create = os.path.normpath(
        os.path.join(output_dir, rendered_dirname)
    )
    make_sure_path_exists(dir_to_create)
    return dir_to_create


def ensure_dir_is_templated(dirname):
    """
    Ensures that dirname is a templated directory name.
    """
    if '{{' in dirname and '}}' in dirname:
        return True
    else:
        raise NonTemplatedInputDirException


def generate_files(repo_dir, context=None, output_dir="."):
    """
    Renders the templates and saves them to files.

    :param repo_dir: Project template input directory.
    :param context: Dict for populating the template's variables.
    :param output_dir: Where to output the generated project dir into.
    """

    template_dir = find_template(repo_dir)
    logging.debug('Generating project from {0}...'.format(template_dir))
    context = context or {}

    unrendered_dir = os.path.split(template_dir)[1]
    ensure_dir_is_templated(unrendered_dir)
    project_dir = render_and_create_dir(unrendered_dir, context, output_dir)

    # We want the Jinja path and the OS paths to match. Consequently, we'll:
    #   + CD to the template folder
    #   + Set Jinja's path to "."
    #
    #  In order to build our files to the correct folder(s), we'll use an
    # absolute path for the target folder (project_dir)

    project_dir = os.path.abspath(project_dir)
    logging.debug("project_dir is {0}".format(project_dir))

    # run pre-gen hook from repo_dir
    with work_in(repo_dir):
        run_hook('pre_gen_project', project_dir)

    with work_in(template_dir):
        env = Environment()
        env.loader = FileSystemLoader(".")

        for root, dirs, files in os.walk("."):
            for d in dirs:
                unrendered_dir = os.path.join(
                    project_dir,
                    os.path.join(root, d))
                render_and_create_dir(unrendered_dir, context, output_dir)

            for f in files:
                infile = os.path.join(root, f)
                logging.debug("f is {0}".format(f))
                generate_file(project_dir, infile, context, env)

    # run post-gen hook from repo_dir
    with work_in(repo_dir):
        run_hook('post_gen_project', project_dir)

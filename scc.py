#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright (C) 2012 Glencoe Software, Inc. All Rights Reserved.
# Use is subject to license terms supplied in LICENSE.txt
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""

Git management script for the Open Microscopy Environment (OME)
This script is used to simplify various branching workflows
by wrapping both local git and Github access.

See the documentation on each Command subclass for specifics.

Environment variables:
    SCC_DEBUG_LEVEL     default: logging.INFO

"""

import os
import sys
import time
import github  # PyGithub
import subprocess
import logging
import threading
import argparse
import difflib

SCC_DEBUG_LEVEL = logging.INFO
if "SCC_DEBUG_LEVEL" in os.environ:
    try:
        log_level = int(os.environ.get("SCC_DEBUG_LEVEL"))
    except:
        log_level = 10 # Assume poorly formatted means "debug"


#
# Public global functions
#

def git_config(name, user=False, local=False, value=None):
    dbg = logging.getLogger("scc.config").debug
    try:
        pre_cmd = ["git", "config"]
        if value is None:
            post_cmd = ["--get", name]
        else:
            post_cmd = [name, value]

        if user:
            pre_cmd.append("--global")
        elif local:
            pre_cmd.append("--local")
        p = subprocess.Popen(pre_cmd + post_cmd, \
                stdout=subprocess.PIPE).communicate()[0]
        value = p.split("\n")[0].strip()
        if value:
            dbg("Found %s", name)
            return value
        else:
            return None
    except Exception:
        dbg("Error retrieving %s", name, exc_info=1)
        value = None
    return value


def get_token(local=False):
    """
    Get the Github API token.
    """
    return git_config("github.token", local=local)


def get_token_or_user(local=False):
    """
    Get the Github API token or the Github user if undefined.
    """
    token = get_token()
    if not token:
        token = git_config("github.user", local=local)
    return token


def get_github(login_or_token=None, password=None, **kwargs):
    """
    Create a Github instance. Can be constructed using an OAuth2 token,
    a Github login and password or anonymously.
    """
    return GHManager(login_or_token, password, **kwargs)


#
# Management classes. These allow for proper mocking in tests.
#


class GHManager(object):
    """
    By setting dont_ask to true, it's possible to prevent the call
    to getpass.getpass. This is useful during unit tests.
    """

    FACTORY = github.Github

    def __init__(self, login_or_token=None, password=None, dont_ask=False):
        self.log = logging.getLogger("scc.gh")
        self.dbg = self.log.debug
        self.login_or_token = login_or_token
        self.dont_ask = dont_ask
        try:
            self.authorize(password)
        except github.GithubException, ge:
            if ge.status == 401:
                msg = ge.data.get("message", "")
                if "Bad credentials" == msg:
                    print msg
                    sys.exit(ge.status)

    def authorize(self, password):
        if password is not None:
            self.create_instance(self.login_or_token, password)
        elif self.login_or_token is not None:
            try:
                self.create_instance(self.login_or_token)
                self.get_login() # Trigger
            except github.GithubException:
                if self.dont_ask:
                    raise
                import getpass
                msg = "Enter password for user %s:" % self.login_or_token
                password = getpass.getpass(msg)
                if password is not None:
                    self.create_instance(self.login_or_token, password)
        else:
            self.create_instance()

    def get_login(self):
        return self.github.get_user().login

    def create_instance(self, *args, **kwargs):
        self.github = self.FACTORY(*args, **kwargs)

    def __getattr__(self, key):
        self.dbg("github.%s", key)
        return getattr(self.github, key)

    def get_rate_limiting(self):
        requests = self.github.rate_limiting
        self.dbg("Remaining requests: %s out of %s", requests[0], requests[1])

    def gh_repo(self, reponame, username=None):
        """
        Github repository are constructed by passing the user and the
        repository name as in https://github.com/username/reponame.git
        """
        if username is None:
            username = self.get_login()
        return GitHubRepository(self, username, reponame)


    def git_repo(self, path, *args, **kwargs):
        """
        Git repository instances are constructed by passing the path
        of the directory containing the repository.
        """
        return GitRepository(self, os.path.abspath(path), *args, **kwargs)



#
# Utility classes
#

class HelpFormatter(argparse.RawTextHelpFormatter):
    """
    argparse.HelpFormatter subclass which cleans up our usage, preventing very long
    lines in subcommands.

    Borrowed from omero/cli.py
    """

    def __init__(self, prog, indent_increment=2, max_help_position=40, width=None):
        argparse.RawTextHelpFormatter.__init__(self, prog, indent_increment, max_help_position, width)
        self._action_max_length = 20

    def _split_lines(self, text, width):
        return [text.splitlines()[0]]

    class _Section(argparse.RawTextHelpFormatter._Section):

        def __init__(self, formatter, parent, heading=None):
            #if heading:
            #    heading = "\n%s\n%s" % ("=" * 40, heading)
            argparse.RawTextHelpFormatter._Section.__init__(self, formatter, parent, heading)


class LoggerWrapper(threading.Thread):
    """
    Read text message from a pipe and redirect them
    to a logger (see python's logger module),
    the object itself is able to supply a file
    descriptor to be used for writing

    fdWrite ==> fdRead ==> pipeReader

    See: http://codereview.stackexchange.com/questions/6567/how-to-redirect-a-subprocesses-output-stdout-and-stderr-to-logging-module
    """

    def __init__(self, logger, level=logging.DEBUG):
        """
        Setup the object with a logger and a loglevel
        and start the thread
        """

        # Initialize the superclass
        threading.Thread.__init__(self)

        # Make the thread a Daemon Thread (program will exit when only daemon
        # threads are alive)
        self.daemon = True

        # Set the logger object where messages will be redirected
        self.logger = logger

        # Set the log level
        self.level = level

        # Create the pipe and store read and write file descriptors
        self.fdRead, self.fdWrite = os.pipe()

        # Create a file-like wrapper around the read file descriptor
        # of the pipe, this has been done to simplify read operations
        self.pipeReader = os.fdopen(self.fdRead)

        # Start the thread
        self.start()
    # end __init__

    def fileno(self):
        """
        Return the write file descriptor of the pipe
        """
        return self.fdWrite
    # end fileno

    def run(self):
        """
        This is the method executed by the thread, it
        simply read from the pipe (using a file-like
        wrapper) and write the text to log.
        NB the trailing newline character of the string
           read from the pipe is removed
        """

        # Endless loop, the method will exit this loop only
        # when the pipe is close that is when a call to
        # self.pipeReader.readline() returns an empty string
        while True:

            # Read a line of text from the pipe
            message_from_pipe = self.pipeReader.readline()

            # If the line read is empty the pipe has been
            # closed, do a cleanup and exit
            # WARNING: I don't know if this method is correct,
            #          further study needed
            if len(message_from_pipe) == 0:
                self.pipeReader.close()
                os.close(self.fdRead)
                return
            # end if

            # Remove the trailing newline character frm the string
            # before sending it to the logger
            if message_from_pipe[-1] == os.linesep:
                message_to_log = message_from_pipe[:-1]
            else:
                message_to_log = message_from_pipe
            # end if

            # Send the text to the logger
            self._write(message_to_log)
        # end while
    # end run

    def _write(self, message):
        """
        Utility method to send the message
        to the logger with the correct loglevel
        """
        self.logger.log(self.level, message)
    # end write


class PullRequest(object):
    def __init__(self, repo, pull):
        """Register the Pull Request and its corresponding Issue"""
        self.pull = pull
        self.issue = repo.get_issue(self.get_number())
        dbg("login = %s", self.get_login())
        dbg("labels = %s", self.get_labels())
        dbg("base = %s", self.get_base())
        dbg("len(comments) = %s", len(self.get_comments()))

    def __contains__(self, key):
        return key in self.get_labels()

    def __repr__(self):
        return "  # PR %s %s '%s'" % (self.get_number(), self.get_login(), self.get_title())

    def test_directories(self):
        directories = []
        for comment in self.get_comments():
            lines = comment.splitlines()
            for line in lines:
                if line.startswith("--test"):
                    directories.append(line.replace("--test", ""))
        return directories

    def get_title(self):
        """Return the title of the Pull Request."""
        return self.pull.title

    def get_user(self):
        """Return the name of the Pull Request owner."""
        return self.pull.user

    def get_login(self):
        """Return the login of the Pull Request owner."""
        return self.pull.user.login

    def get_number(self):
        """Return the number of the Pull Request."""
        return int(self.pull.issue_url.split("/")[-1])

    def get_head_login(self):
        """Return the login of the branch where the changes are implemented."""
        return self.pull.head.user.login

    def get_sha(self):
        """Return the SHA1 of the head of the Pull Request."""
        return self.pull.head.sha

    def get_base(self):
        """Return the branch against which the Pull Request is opened."""
        return self.pull.base.ref

    def get_labels(self):
        """Return the labels of the Pull Request."""
        return [x.name for x in  self.issue.labels]

    def get_comments(self):
        """Return the labels of the Pull Request."""
        if self.issue.comments:
            return [comment.body for comment in self.issue.get_comments()]
        else:
            return []

class GitHubRepository(object):

    def __init__(self, gh, user_name, repo_name):
        self.log = logging.getLogger("scc.repo")
        self.gh = gh
        self.user_name = user_name
        self.repo_name = repo_name

        try:
            self.repo = gh.get_user(user_name).get_repo(repo_name)
            if self.repo.organization:
                self.org = gh.get_organization(self.repo.organization.login)
            else:
                self.org = None
        except:
            self.log.error("Failed to find %s/%s", user_name, repo_name)
            raise

    def __getattr__(self, key):
        return getattr(self.repo, key)

    def get_owner(self):
        return self.owner.login

    def is_whitelisted(self, user):
        if self.org:
            status = self.org.has_in_public_members(user)
        else:
            status = False
        return status

    def push(self, name):
        # TODO: We need to make it possible
        # to create a GitRepository object
        # with only a remote connection for
        # just those actions which don't
        # require a clone.
        repo = "git@github.com:%s/%s.git" % (self.get_owner(), self.repo_name)
        p = subprocess.Popen(["git", "push", repo, name])
        rc = p.wait()
        if rc != 0:
            raise Exception("'git push %s %s' failed", repo, name)

    def open_pr(self, title, description, base, head):
        return self.repo.create_pull(title, description, base, head)


class GitRepository(object):

    def __init__(self, gh, path, reset=False):
        """
        Register the git repository path, return the current status and
        register the Github origin remote.
        """

        self.log = logging.getLogger("scc.git")
        self.dbg = self.log.debug
        self.info = self.log.info
        self.debugWrap = LoggerWrapper(self.log, logging.DEBUG)
        self.infoWrap = LoggerWrapper(self.log, logging.INFO)

        self.gh = gh
        self.path =  os.path.abspath(path)

        if reset:
            self.reset()
        self.get_status()

        self.reset = reset

        # Register the origin remote
        [user_name, repo_name] = self.get_remote_info("origin")
        self.origin = gh.gh_repo(repo_name, user_name)
        self.candidate_pulls = []

    def cd(self, directory):
        if not os.path.abspath(os.getcwd()) == os.path.abspath(directory):
            self.dbg("cd %s", directory)
            os.chdir(directory)

    def communicate(self, *command):
        self.dbg("Calling '%s' for stdout/err" % " ".join(command))
        p = subprocess.Popen(command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        o, e = p.communicate()
        if p.returncode:
            msg = """Failed to run '%s'
    rc:     %s
    stdout: %s
    stderr: %s""" % (" ".join(command), p.returncode, o, e)
            raise Exception(msg)
        return o, e

    def call_info(self, *command, **kwargs):
        """
        Call wrap_call with a info LoggerWrapper
        """
        return self.wrap_call(self.infoWrap, *command, **kwargs)

    def call(self, *command, **kwargs):
        """
        Call wrap_call with a debug LoggerWrapper
        """
        return self.wrap_call(self.debugWrap, *command, **kwargs)

    def wrap_call(self, logWrap, *command, **kwargs):
        for x in ("stdout", "stderr"):
            if x not in kwargs:
                kwargs[x] = logWrap
        self.dbg("Calling '%s'" % " ".join(command))
        p = subprocess.Popen(command, **kwargs)
        rc = p.wait()
        if rc:
            raise Exception("rc=%s" % rc)
        return p

    def find_candidates(self, filters):
        """Find candidate Pull Requests for merging."""
        self.dbg("## PRs found:")
        directories_log = None

        # Loop over pull requests opened aainst base
        pulls = [pull for pull in self.origin.get_pulls() if (pull.base.ref == filters["base"])]
        for pull in pulls:
            pullrequest = PullRequest(self.origin, pull)
            labels = [x.lower() for x in pullrequest.get_labels()]

            found = self.origin.is_whitelisted(pullrequest.get_user())

            if not found:
                if filters["include"]:
                    whitelist = [filt for filt in filters["include"] if filt.lower() in labels]
                    if whitelist:
                        self.dbg("# ... Include %s", whitelist)
                        found = True

            if not found:
                continue

            # Exclude PRs if exclude labels are input
            if filters["exclude"]:
                blacklist = [filt for filt in filters["exclude"] if filt.lower() in labels]
                if blacklist:
                    self.dbg("# ... Exclude %s", blacklist)
                    continue

            if found:
                self.dbg(pullrequest)
                self.candidate_pulls.append(pullrequest)
                directories = pullrequest.test_directories()
                if directories:
                    if directories_log == None:
                        directories_log = open('directories.txt', 'w')
                    for directory in directories:
                        directories_log.write(directory)
                        directories_log.write("\n")
        self.candidate_pulls.sort(lambda a, b: cmp(a.get_number(), b.get_number()))

        # Cleanup
        if directories_log:
            directories_log.close()

    #
    # General git commands
    #

    def get_current_head(self):
        """Return the symbolic name for the current branch"""
        self.cd(self.path)
        self.dbg("Get current head")
        o, e = self.communicate("git", "symbolic-ref", "HEAD")
        o = o.strip()
        refsheads = "refs/heads/"
        if o.startswith(refsheads):
            o = o[len(refsheads):]
        return o

    def get_current_sha1(self):
        """Return the sha1 for the current commit"""
        self.cd(self.path)
        self.dbg("Get current sha1")
        o, e = self.communicate("git", "rev-parse", "HEAD")
        return o.strip()

    def get_status(self):
        """Return the status of the git repository including its submodules"""
        self.cd(self.path)
        self.dbg("Check current status")
        self.call("git", "log", "--oneline", "-n", "1", "HEAD")
        self.call("git", "submodule", "status")

    def add(self, file):
        """
        Add a file to the repository. The path should
        be relative to the top of the repository.
        """
        self.cd(self.path)
        self.dbg("Adding %s...", file)
        self.call("git", "add", file)

    def commit(self, msg):
        self.cd(self.path)
        self.dbg("Committing %s...", msg)
        self.call("git", "commit", "-m", msg)

    def new_branch(self, name, head="HEAD"):
        self.cd(self.path)
        self.dbg("New branch %s from %s...", name, head)
        self.call("git", "checkout", "-b", name, head)

    def checkout_branch(self, name):
        self.cd(self.path)
        self.dbg("Checkout branch %s...", name)
        self.call("git", "checkout", name)

    def add_remote(self, name, url=None):
        self.cd(self.path)
        if url is None:
            repo_name = self.origin.repo.name
            url = "git@github.com:%s/%s.git" % (name, repo_name)
        self.dbg("Adding remote %s for %s...", name, url)
        self.call("git", "remote", "add", name, url)

    def push_branch(self, name, remote="origin"):
        self.cd(self.path)
        self.dbg("Pushing branch %s to %s..." % (name, remote))
        self.call("git", "push", remote, name)

    def delete_local_branch(self, name, force=False):
        self.cd(self.path)
        self.dbg("Deleting branch %s locally..." % name)
        d_switch = force and "-D" or "-d"
        self.call("git", "branch", d_switch, name)

    def delete_branch(self, name, remote="origin"):
        self.cd(self.path)
        self.dbg("Deleting branch %s from %s..." % (name, remote))
        self.call("git", "push", remote, ":%s" % name)

    def reset(self):
        """Reset the git repository to its HEAD"""
        self.cd(self.path)
        self.dbg("Resetting...")
        self.call("git", "reset", "--hard", "HEAD")

    def fast_forward(self, base, remote = "origin"):
        """Execute merge --ff-only against the current base"""
        self.dbg("## Merging base to ensure closed PRs are included.")
        p = subprocess.Popen(["git", "merge", "--ff-only", "%s/%s" % (remote, base)], stdout = subprocess.PIPE).communicate()[0]
        self.info(p.rstrip("/n"))

    def rebase(self, newbase, upstream, sha1):
        self.call_info("git", "rebase", "--onto", \
                "%s" % newbase, "%s" % upstream, "%s" % sha1)

    def get_rev_list(self, commit):
        revlist_cmd = lambda x: ["git","rev-list","--first-parent","%s" % x]
        p = subprocess.Popen(revlist_cmd(commit), stdout = subprocess.PIPE, stderr = subprocess.PIPE)
        self.dbg("Calling '%s'" % " ".join(revlist_cmd(commit)))
        (revlist, stderr) = p.communicate('')

        if stderr or p.returncode:
            print "Error output was:\n%s" % stderr
            print "Output was:\n%s" % revlist
            return False

        return revlist.splitlines()

    #
    # Higher level git commands
    #

    def info(self):
        """List the candidate Pull Request to be merged"""
        for pullrequest in self.candidate_pulls:
            print "# %s" % " ".join(pullrequest.get_labels())
            print "%s %s by %s for \t\t[???]" % \
                (pullrequest.pr.issue_url, pullrequest.get_title(), pullrequest.get_login())
            print

    def get_remote_info(self, remote_name):
        """
        Return user and repository name of the specified remote.

        Origin remote must be on Github, i.e. of type
        *github/user/repository.git
        """
        self.cd(self.path)
        originurl = self.call("git", "config", "--get", \
            "remote." + remote_name + ".url", stdout = subprocess.PIPE, \
            stderr = subprocess.PIPE).communicate()[0]

        # Read user from origin URL
        dirname = os.path.dirname(originurl)
        assert "github" in dirname, 'Origin URL %s is not on GitHub' % dirname
        user = os.path.basename(dirname)
        if ":" in dirname:
            user = user.split(":")[-1]

        # Read repository from origin URL
        basename = os.path.basename(originurl)
        repo = os.path.splitext(basename)[0]
        self.info("Repository: %s/%s", user, repo)
        return [user , repo]

    def merge(self, comment=False, commit_id = "merge"):
        """Merge candidate pull requests."""
        self.dbg("## Unique users: %s", self.unique_logins())
        for key, url in self.remotes().items():
            self.call("git", "remote", "add", key, url)
            self.call("git", "fetch", key)

        conflicting_pulls = []
        merged_pulls = []

        for pullrequest in self.candidate_pulls:
            premerge_sha, e = self.call("git", "rev-parse", "HEAD", stdout = subprocess.PIPE).communicate()
            premerge_sha = premerge_sha.rstrip("\n")

            try:
                self.call("git", "merge", "--no-ff", "-m", \
                        "%s: PR %s (%s)" % (commit_id, pullrequest.get_number(), pullrequest.get_title()), pullrequest.get_sha())
                merged_pulls.append(pullrequest)
            except:
                self.call("git", "reset", "--hard", "%s" % premerge_sha)
                conflicting_pulls.append(pullrequest)

                msg = "Conflicting PR."
                job_dict = ["JOB_NAME", "BUILD_NUMBER", "BUILD_URL"]
                if all([key in os.environ for key in job_dict]):
                    job_values = [os.environ.get(key) for key in job_dict]
                    msg += " Removed from build [%s#%s](%s). See the [console output](%s) for more details." % \
                        (job_values[0], job_values[1], job_values[2], job_values[2] +"/consoleText")
                self.dbg(msg)

                if comment and get_token():
                    self.dbg("Adding comment to issue #%g." % pullrequest.get_number())
                    pullrequest.issue.create_comment(msg)

        if merged_pulls:
            self.info("Merged PRs:")
            for merged_pull in merged_pulls:
                self.info(merged_pull)

        if conflicting_pulls:
            self.info("Conflicting PRs (not included):")
            for conflicting_pull in conflicting_pulls:
                self.info(conflicting_pull)

        self.call("git", "submodule", "update")

    def find_branching_point(self, topic_branch, main_branch):
        # See http://stackoverflow.com/questions/1527234/finding-a-branch-point-with-git
        topic_revlist = self.get_rev_list(topic_branch)
        main_revlist = self.get_rev_list(main_branch)

        # Compare sequences
        s = difflib.SequenceMatcher(None, topic_revlist, main_revlist)
        matching_block = s.get_matching_blocks()
        if matching_block[0].size == 0:
            raise Exception("No matching block found")

        sha1 = main_revlist[matching_block[0].b]
        self.info("Branching SHA1: %s" % sha1[0:6])
        return sha1

    def submodules(self, filters, info=False, comment=False, commit_id = "merge"):
        """Recursively merge PRs for each submodule."""

        submodule_paths = self.call("git", "submodule", "--quiet", "foreach", \
                "echo $path", \
                stdout=subprocess.PIPE).communicate()[0]

        cwd = os.path.abspath(os.getcwd())
        lines = submodule_paths.split("\n")
        while "".join(lines):
            directory = lines.pop(0).strip()
            try:
                submodule_repo = None
                submodule_repo = self.gh.git_repo(directory, self.reset)
                if info:
                    submodule_repo.info()
                else:
                    submodule_repo.fast_forward(filters["base"])
                    submodule_repo.find_candidates(filters)
                    submodule_repo.merge(comment)
                submodule_repo.submodules(info)
            finally:
                try:
                    if submodule_repo:
                        submodule_repo.cleanup()
                finally:
                    self.cd(cwd)

        self.call("git", "commit", "--allow-empty", "-a", "-n", "-m", \
                "%s: Update all modules w/o hooks" % commit_id)

    def unique_logins(self):
        """Return a set of unique logins."""
        unique_logins = set()
        for pull in self.candidate_pulls:
            unique_logins.add(pull.get_head_login())
        return unique_logins

    def remotes(self):
        """Return remotes associated to unique login."""
        remotes = {}
        for user in self.unique_logins():
            key = "merge_%s" % user
            if self.origin.private:
                url = "git@github.com:%s/%s.git"  % (user, self.origin.name)
            else:
                url = "git://github.com/%s/%s.git" % (user, self.origin.name)
            remotes[key] = url
        return remotes

    def cleanup(self):
        """Remove remote branches created for merging."""
        for key in self.remotes().keys():
            try:
                self.call("git", "remote", "rm", key)
            except Exception:
                self.log.error("Failed to remove", key, exc_info=1)


#
# What follows are the commands which are available from the command-line.
# Alphabetically listed please.
#

class Command(object):
    """
    Base type. At the moment just a marker class which
    signifies that a subclass is a CLI command. Subclasses
    should register themselves with the parser during
    instantiation. Note: Command.__call__ implementations
    are responsible for calling cleanup()
    """

    NAME = "abstract"

    def __init__(self, sub_parsers):
        self.log = logging.getLogger("scc.%s"%self.NAME)
        self.log_level = SCC_DEBUG_LEVEL

        help = self.__doc__.lstrip()
        self.parser = sub_parsers.add_parser(self.NAME,
            help=help, description=help)
        self.parser.set_defaults(func=self.__call__)

        self.parser.add_argument("-v", "--verbose", action="count", default=0,
            help="Increase the logging level by multiples of 10")
        self.parser.add_argument("-q", "--quiet", action="count", default=0,
            help="Decrease the logging level by multiples of 10")

    def add_token_args(self):
        self.parser.add_argument("--token",
            help="Token to use rather than from config files")
        self.parser.add_argument("--no-ask", action='store_true',
            help="Don't ask for a password if token usage fails")

    def __call__(self, args):
        self.configure_logging(args)
        self.cwd = os.path.abspath(os.getcwd())

    def login(self, args):
        if args.token:
            token = args.token
        else:
            token = get_token_or_user()
        if token is None and not args.no_ask:
            print "# github.token and github.user not found."
            print "# See `%s token` for simpifying use." % sys.argv[0]
            token = raw_input("Username or token: ").strip()
        self.gh = get_github(token, dont_ask=args.no_ask)

    def configure_logging(self, args):
        self.log_level += args.quiet * 10
        self.log_level -= args.verbose * 10

        log_format = """%(asctime)s %(levelname)-5.5s [%(name)12.12s] %(message)s"""
        logging.basicConfig(level=self.log_level, format=log_format)
        logging.getLogger('github').setLevel(logging.INFO)


class CleanSandbox(Command):
    """Cleans snoopys-sandbox repo after testing

Removes all branches from your fork of snoopys-sandbox
    """

    NAME = "clean-sandbox"

    def __init__(self, sub_parsers):
        super(CleanSandbox, self).__init__(sub_parsers)
        self.add_token_args()

        group = self.parser.add_mutually_exclusive_group(required=True)
        group.add_argument('-f', '--force', action="store_true",
                help="Perform a clean of all non-master branches")
        group.add_argument('-n', '--dry-run', action="store_true",
                help="Perform a dry-run without removing any branches")

        self.parser.add_argument("--skip", action="append", default=["master"])

    def __call__(self, args):
        super(CleanSandbox, self).__call__(args)
        self.login(args)

        gh_repo = self.gh.gh_repo("snoopys-sandbox")
        branches = gh_repo.repo.get_branches()
        for b in branches:
            if b.name in args.skip:
                if args.dry_run:
                    print "Would not delete", b.name
            elif args.dry_run:
                print "Would delete", b.name
            elif args.force:
                gh_repo.push(":%s" % b.name)
            else:
                raise Exception("Not possible!")


class Merge(Command):
    """
    Merge Pull Requests opened against a specific base branch.

    Automatically merge all pull requests with any of the given labels.
    It assumes that you have checked out the target branch locally and
    have updated any submodules. The SHA1s from the PRs will be merged
    into the current branch. AFTER the PRs are merged, any open PRs for
    each submodule with the same tags will also be merged into the
    CURRENT submodule sha1. A final commit will then update the submodules.
    """

    NAME = "merge"

    def __init__(self, sub_parsers):
        super(Merge, self).__init__(sub_parsers)
        self.add_token_args()

        self.parser.add_argument('--reset', action='store_true',
            help='Reset the current branch to its HEAD')
        self.parser.add_argument('--info', action='store_true',
            help='Display merge candidates but do not merge them')
        self.parser.add_argument('--comment', action='store_true',
            help='Add comment to conflicting PR')
        self.parser.add_argument('base', type=str)
        self.parser.add_argument('--include', nargs="*",
            help='PR labels to include in the merge')
        self.parser.add_argument('--exclude', nargs="*",
            help='PR labels to exclude from the merge')
        self.parser.add_argument('--buildnumber', type=int, default=None,
            help='The build number to use to push to team.git')

    def __call__(self, args):
        super(Merge, self).__call__(args)
        self.login(args)

        main_repo = self.gh.git_repo(self.cwd, args.reset)

        try:
            self.merge(args, main_repo)
        finally:
            main_repo.cleanup()

    def merge(self, args, main_repo):
        self.log.info("Merging PR based on: %s", args.base)
        self.log.info("Excluding PR labelled as: %s", args.exclude)
        self.log.info("Including PR labelled as: %s", args.include)

        filters = {}
        filters["base"] = args.base
        filters["include"] = args.include
        filters["exclude"] = args.exclude
        main_repo.find_candidates(filters)

        def commit_id(filters):
            """
            Return commit identifier generated from base branch, include and
            exclude labels.
            """
            commit_id = "merge"+"_into_"+filters["base"]
            if filters["include"]:
                commit_id += "+" + "+".join(filters["include"])
            if filters["exclude"]:
                commit_id += "-" + "-".join(filters["exclude"])
            return commit_id

        if not args.info:
            main_repo.merge(args.comment, commit_id = commit_id(filters))

        main_repo.submodules(filters, args.info, args.comment, commit_id = commit_id(filters))  # Recursive

        if args.buildnumber:
            newbranch = "HEAD:%s/%g" % (args.base, args.build_number)
            call("git", "push", "team", newbranch)


class Rebase(Command):
    """Rebase Pull Requests opened against a specific base branch.

        The workflow currently is:

        1) Find the branch point for the original PR.
        2) Rebase all commits from the branch point to the tip.
        3) Create a branch named "rebase/develop/ORIG_NAME".
        4) If push is set, also push to GH, and switch branches.
        5) If pr is set, push to GH, open a PR, and switch branches.
        6) If keep is set, omit the deleting of the newbranch."""

    NAME = "rebase"

    def __init__(self, sub_parsers):
        super(Rebase, self).__init__(sub_parsers)
        self.add_token_args()

        for name, help in (
                ('pr', 'Skip creating a PR.'),
                ('push', 'Skip pushing github'),
                ('delete', 'Skip deleting local branch')):

            self.parser.add_argument('--no-%s'%name, action='store_false',
                dest=name, default=True, help=help)

        self.parser.add_argument('--remote', default="origin",
            help='Name of the remote to use as the origin')

        self.parser.add_argument('PR', type=int, help="The number of the pull request to rebase")
        self.parser.add_argument('newbase', type=str, help="The branch of origin onto which the PR should be rebased")

    def __call__(self, args):
        super(Rebase, self).__call__(args)
        self.login(args)

        main_repo = self.gh.git_repo(self.cwd, False)
        try:
            self.rebase(args, main_repo)
        finally:
            main_repo.cleanup()

    def rebase(self, args, main_repo):

        # Local information
        [origin_name, origin_repo] = main_repo.get_remote_info(args.remote)
        # If we are pushing the branch somewhere, we likely will
        # be deleting the new one, and so should remember what
        # commit we are on now in order to go back to it.
        try:
            old_branch = main_repo.get_current_head()
        except:
            old_branch = main_repo.get_current_sha1()

        # Remote information
        pr = main_repo.origin.get_pull(args.PR)
        self.log.info("PR %g: %s opened by %s against %s", \
            args.PR, pr.title, pr.head.user.name, pr.base.ref)
        pr_head = pr.head.sha
        self.log.info("Head: %s", pr_head[0:6])
        self.log.info("Merged: %s", pr.is_merged())

        branching_sha1 = main_repo.find_branching_point(pr_head,
                "%s/%s" % (args.remote, pr.base.ref))
        main_repo.rebase("%s/%s" % (args.remote, args.newbase),
                branching_sha1[0:6], pr_head)

        new_branch = "rebased/%s/%s" % (args.newbase, pr.head.ref)
        main_repo.new_branch(new_branch)
        print >> sys.stderr, "# Created local branch %s" % new_branch

        if args.push or args.pr:
            try:
                user = self.gh.get_login()
                remote = "git@github.com:%s/%s.git" % (user, origin_repo)

                main_repo.push_branch(new_branch, remote=remote)
                print >> sys.stderr, "# Pushed %s to %s" % (new_branch, remote)

                if args.pr:
                    template_args = {"id":pr.number, "base":args.newbase,
                            "title": pr.title, "body": pr.body}
                    title = "%(title)s (rebased onto %(base)s)" % template_args
                    body= """

This is the same as gh-%(id)s but rebased onto %(base)s.

----

%(body)s

                    """ % template_args

                    gh_repo = self.gh.gh_repo(origin_repo, origin_name)
                    pr = gh_repo.open_pr(title, body,
                            base=args.newbase, head="%s:%s" % (user, new_branch))
                    print pr.html_url
                    # Reload in order to prevent mergeable being null.
                    time.sleep(0.5)
                    pr = main_repo.origin.get_pull(pr.number)
                    if not pr.mergeable:
                        print >> sys.stderr, "#"
                        print >> sys.stderr, "# WARNING: PR is NOT mergeable!"
                        print >> sys.stderr, "#"

            finally:
                main_repo.checkout_branch(old_branch)

            if args.delete:
                main_repo.delete_local_branch(new_branch, force=True)


class Token(Command):
    """Get, set, and create tokens for use by scc"""

    NAME = "token"

    def __init__(self, sub_parsers):
        super(Token, self).__init__(sub_parsers)
        # No token args

        self.parser.add_argument("--local", action="store_true",
            help="Access token only in local repository")
        self.parser.add_argument("--user", action="store_true",
            help="Access token only in user configuration")
        self.parser.add_argument("--all", action="store_true",
            help="""Print all known tokens with key""")
        self.parser.add_argument("--set",
            help="Set token to specified value")
        self.parser.add_argument("--create", action="store_true",
            help="""Create token by authorizing with github.""")

    def __call__(self, args):
        super(Token, self).__call__(args)
        # No login

        if args.all:
            for key in ("github.token", "github.user"):

                for user, local, msg in \
                    ((False, True, "local"), (True, False, "user")):

                    rv = git_config(key, user=user, local=local)
                    if rv is not None:
                        print "[%s] %s=%s" % (msg, key, rv)

        elif (args.set or args.create):
            if args.create:
                user = git_config("github.user")
                if not user:
                    raise Exception("No github.user configured")
                gh = get_github(user)
                user = gh.github.get_user()
                auth = user.create_authorization(["public_repo"], "scc token")
                git_config("github.token", user=args.user,
                    local=args.local, value=auth.token)
            else:
                git_config("github.token", user=args.user,
                    local=args.local, value=args.set)
        else:
            token = git_config("github.token",
                user=args.user, local=args.local)
            if token:
                print token


def main(args=None):
    """
    Reusable entry point. Arguments are parsed
    via the argparse-subcommands configured via
    each Command class found in globals().
    """

    if args is None: args = sys.argv[1:]
    scc_parser = argparse.ArgumentParser(
        description='Snoopy Crime Cop Script',
        formatter_class=HelpFormatter)
    sub_parsers = scc_parser.add_subparsers(title="Subcommands")

    for name, MyCommand in sorted(globals().items()):
        if not isinstance(MyCommand, type): continue
        if not issubclass(MyCommand, Command): continue
        if MyCommand == Command: continue
        MyCommand(sub_parsers)

    ns = scc_parser.parse_args(args)
    ns.func(ns)


if __name__ == "__main__":
    main()
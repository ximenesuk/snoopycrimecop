#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright (C) 2012-2013 University of Dundee & Open Microscopy Environment
# All Rights Reserved.
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

import unittest

from scc.framework import main, Stop
from scc.git import Rebase
from Sandbox import SandboxTest
from subprocess import Popen


class RebaseTest(SandboxTest):

    def setUp(self):

        super(RebaseTest, self).setUp()
        self.source_base = "dev_4_4"
        self.target_base = "develop"
        self.source_branch = None
        self.target_branch = None

    def rebase(self, *args):
        args = ["rebase", "--no-ask", str(self.pr.number),
                self.target_base] + list(args)
        main(args=args, items=[(Rebase.NAME, Rebase)])

    def has_remote_source_branch(self):
        return self.source_branch and \
            self.sandbox.has_remote_branch(
                self.source_branch, remote=self.user)

    def has_remote_target_branch(self):
        return self.target_branch and \
            self.sandbox.has_remote_branch(
                self.target_branch, remote=self.user)

    def has_rebased_pr(self):

        # Check the last opened PR is the rebased one
        prs = self.sandbox.origin.get_pulls()
        return prs[0].head.user.login == self.user and \
            prs[0].head.ref == self.target_branch

    def tearDown(self):

        if self.source_branch or self.target_branch:
            self.sandbox.fetch(self.user)
        if self.has_remote_source_branch():
            # Clean the initial branch. This will close the inital PRs
            self.sandbox.push_branch(":%s" % self.source_branch,
                                     remote=self.user)

        if self.has_remote_target_branch():
            # Clean the rebased branch
            self.sandbox.push_branch(":%s" % self.target_branch,
                                     remote=self.user)

        super(RebaseTest, self).tearDown()


class MockPR(object):

    def __init__(self, number):
        self.number = number


class TestRebaseStop(RebaseTest):

    def testUnfoundPR(self):

        self.pr = MockPR(0)
        self.assertRaises(Stop, self.rebase)

    def testNoCommonCommits(self):

        self.pr = MockPR(79)
        self.assertRaises(Stop, self.rebase)

    def testBadObject(self):

        self.pr = MockPR(112)
        self.assertRaises(Stop, self.rebase)


class TestRebaseNewBranch(RebaseTest):

    def setUp(self):

        super(TestRebaseNewBranch, self).setUp()

        # Open first PR against dev_4_4 branch
        self.source_branch = self.fake_branch(head=self.source_base)
        self.pr = self.open_pr(self.source_branch, self.source_base)

        # Define target branch for rebasing PR
        self.target_branch = "rebased/%s/%s" \
            % (self.target_base, self.source_branch)

    def rebase(self, *args):
        args = ["rebase", "--no-ask", str(self.pr.number),
                self.target_base] + list(args)
        main(args=args, items=[(Rebase.NAME, Rebase)])

    def testPushExistingLocalBranch(self):

        # Rebase the PR locally
        self.sandbox.new_branch(self.target_branch)
        self.assertRaises(Stop, self.rebase)

    def testPushExistingRemoteBranch(self):

        self.sandbox.push_branch("HEAD:refs/heads/%s" % (self.target_branch),
                                 remote=self.user)
        self.assertRaises(Stop, self.rebase)

    def testPushLocalRebase(self):

        # Rebase the PR locally
        self.rebase("--no-push", "--no-pr")
        self.assertFalse(self.has_remote_target_branch())

    def testPushNoFetch(self):

        # Rebase the PR locally
        self.rebase("--no-fetch", "--no-push", "--no-pr")

    def testPushRebaseNoPr(self):

        self.rebase("--no-pr")
        self.assertTrue(self.has_remote_target_branch())
        self.assertFalse(self.has_rebased_pr())

    def testDefault(self):

        # Rebase the PR and push to Github
        self.rebase()
        self.assertTrue(self.has_rebased_pr())

    def testRemote(self):

        self.rename_origin_remote("gh")
        self.assertRaises(Stop, self.rebase)
        self.rebase("--remote", "gh")
        self.assertTrue(self.has_rebased_pr())


class TestConflictingRebase(RebaseTest):

    def setUp(self):

        super(TestConflictingRebase, self).setUp()

        # Open first PR against dev_4_4 branch
        self.source_branch = self.uuid()
        self.filename = 'README.md'

        f = open(self.filename, "w")
        f.write("hi")
        f.close()

        self.sandbox.new_branch(self.source_branch, head=self.source_base)
        self.sandbox.add(self.filename)

        self.sandbox.commit("Writing %s" % self.filename)
        self.sandbox.get_status()

        self.pr = self.open_pr(self.source_branch, self.source_base)

        # Define target branch for rebasing PR
        self.target_branch = "rebased/%s/%s" \
            % (self.target_base, self.source_branch)

    def testPushRebaseContinue(self):

        # Rebase the PR locally
        self.assertRaises(Stop, self.rebase)

        f = open(self.filename, "w")
        f.write("hi")
        f.close()

        self.sandbox.add(self.filename)
        p = Popen(["git", "rebase", "--continue"])
        self.assertEquals(0, p.wait())

        self.rebase("--continue")
        self.assertTrue(self.has_rebased_pr())

if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-

#
# Copyright (C) 2013 University of Dundee & Open Microscopy Environment
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

from scc.framework import main
from scc.git import UnrebasedPRs
from Sandbox import SandboxTest


class TestUnrebasedPRs(SandboxTest):

    def setUp(self):
        super(TestUnrebasedPRs, self).setUp()
        self.branch1 = "dev_4_4"
        self.branch2 = "develop"

    def unrebased_prs(self, *args):
        args = ["unrebased-prs", self.branch1, self.branch2] + list(args)
        main(args=args, items=[(UnrebasedPRs.NAME, UnrebasedPRs)])

    def testUnrebasedPRs(self):
        self.unrebased_prs()

if __name__ == '__main__':
    import logging
    logging.basicConfig()
    unittest.main()
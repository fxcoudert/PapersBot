#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# license:  MIT License
# author:   François-Xavier Coudert
# e-mail:   fxcoudert@gmail.com
#

import datetime
import sys
import git

assert len(sys.argv) == 2

repo = git.Repo(sys.argv[1])
commits = list(repo.iter_commits(paths='posted.dat', max_count=1))
assert len(commits) == 1

stamp = commits[0].committed_datetime
delta = datetime.datetime.now(stamp.tzinfo) - stamp
hours_ago = delta.total_seconds() / (60 * 60)

assert hours_ago > 0

print(f"Last commit to 'posted.dat' was {hours_ago:.2f} hours ago")

if hours_ago > 12:
    print("ERROR: lack of activity, check actions for problems")
    sys.exit(1)

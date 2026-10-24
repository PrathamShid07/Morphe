# Git workflow

## Branching

A Git branch is a lightweight pointer to a commit. Create one with git switch -c feature and keep each branch focused on a single change so pull requests stay small and reviewable.

## Merge versus rebase

git merge joins histories with a merge commit and preserves what happened. git rebase replays commits on top of another base for a linear history. Never rebase commits that teammates already pulled.

## Stash and reflog

git stash saves uncommitted changes so you can switch branches, and git stash pop restores them. The reflog records where HEAD pointed, so git reflog helps recover commits after a bad reset.

## Cherry pick and revert

git cherry-pick applies one commit from another branch onto the current branch. git revert creates a new commit that undoes an earlier one without rewriting history, which is safe on shared branches.

## Resolving conflicts

Conflict markers show both versions between <<<<<<< and >>>>>>>. Edit the file, git add it and continue with git merge --continue or git rebase --continue. git bisect finds the commit that introduced a bug by binary search.

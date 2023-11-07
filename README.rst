`r3l3453` is a small project that I use for semi-automating the release cycle on a few projects of mine. It expects the project to have a very specific configuration that I personally prefer, and therefore may not be suitable for other's projects.

In short what it does is as follows:

* Bump version(s) to a release version according to git log and `Conventional Commits`_.
* Change the title of `Unreleased`_ section in ``CHANGELOG.rst`` to the new version.
* Commit changes.
* Tag the commit.
* Release to PyPI.
* Bump the version again to dev0 version for the next release.
* Push changes to repository.

There is a ``--simulate`` cli option which allows one to see what is going to happen.

.. _Conventional Commits: https://www.conventionalcommits.org/
.. _Unreleased: https://keepachangelog.com/

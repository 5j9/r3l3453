`r3l3453` is a small project that I use for semi-automating release cycle on a few of my projects.

In short what it does is as follows:

* Bump version(s) to a release version according to git log and `Conventional Commits`_.
* Change the title of `Unreleased`_ section in ``CHANGELOG.rst`` to the new version.
* Commit changes.
* Tag the commit.
* Release to PyPI.
* Bump the version again to dev0 version for the next release.
* Push changes to repository.

``r3l3453.json`` can be used to specify the location of version variables.
Refer to the ``r3l3453.json`` of this project to see how it is used.

There is a ``--simulate`` cli option which allows one to see what is going to happen.

.. _Conventional Commits: https://www.conventionalcommits.org/
.. _Unreleased: https://keepachangelog.com/

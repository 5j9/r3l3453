`r3l3453` is a small project that I use for semi-automating release cycle on a few of my projects.
It's not intended for public use.

In short what it does is:

* bump the version to a release version
* tag the version
* commit the version change
* release to PyPI
* bump the version again to dev version
* push changes to repository

There is a ``--simulate`` cli option which allows one to see what is going to happen.

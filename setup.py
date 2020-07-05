from os.path import abspath, dirname

from setuptools import setup


with open('README.rst', 'r') as fh:
    long_description = fh.read()

here = abspath(dirname(__file__))
setup(
    name='release',
    version='0.1.dev0',
    author='5j9',
    author_email='5j9@users.noreply.github.com',
    description=(
        'Bump version, tag, commit, release to pypi, bump again, and push.'),
    license='GNU General Public License v3 (GPLv3)',
    long_description=long_description,
    long_description_content_type='text/x-rst',
    url='https://github.com/5j9/release',
    packages=['release'],
    python_requires='>=3.9',
    install_requires=['path', 'parver', 'tomlkit'],
    classifiers=[
        'Development Status :: 1 - Planning',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Natural Language :: English',
        'Programming Language :: Python :: 3.9',
        'Topic :: Software Development',
        'Topic :: Software Development :: Build Tools'],
    zip_safe=True)

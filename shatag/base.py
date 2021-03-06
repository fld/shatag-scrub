#!/usr/bin/env python3
# Copyright 2010 Maxime Augier
# Distributed under the terms of the GNU General Public License

#import argparse
import hashlib
import os
import os.path
import socket
import sys
import yaml

def chost():
    hostname = socket.gethostname()
    try:
        (canonical,aliases,addresses) = socket.gethostbyaddr(socket.gethostname())
        return canonical
    except socket.gaierror:
        return hostname

def hashfile (filename):
    bs=4096
    sha256 = hashlib.sha256()
    with open(filename, 'rb') as fd:    
        while True:
            data = fd.read(bs)
            if not data:
                break
            sha256.update(data)

    return sha256.hexdigest()

class NoChecksum(Exception):
    pass

class IFile(object):
    """Represents an abstract checksummed file in the FS."""
    def __init__(self, filename, db=None):
        """Creates a file object. This will load the corresponding timestamp and xattrs from the filesystem."""
        self.filename = filename 
        self.db = db
        self.mtime = str(os.stat(filename).st_mtime)

        self.ts = None
        self.shatag = None

        self.read()

        if self.mtime == self.ts:
            self.state = 'good'
        elif self.ts is None:      
            self.state = 'missing'
        else:
            self.state = 'bad'

    def fullpath(self):
        """Absolute path of the file object"""
        return os.path.abspath(self.filename)

    def path(self, canonical=False):
        """If canonical is true, equivalent to fullpath, otherwise file name"""
        if canonical:
            return self.fullpath()
        else:
            return self.filename

    def update(self):
        """Rehash the file only if there is an existing, outdated hash"""
        if self.state == 'bad': 
            self.rehash()

    def tag(self):
        """Rehash the file if there is an outated hash or no hash at all"""
        if self.state == 'missing' or self.state == 'bad':
            self.rehash()

    def show(self, canonical=False):
        """Show the hash and filename in sha256(1) compatible format"""
        if self.state == 'good':
            return self.shatag + '  ' + self.path(canonical)
        else:
            raise NoChecksum()

    def verbose(self, canonical=False):
        """Print warnings if the checksum is missing or outdated"""
        if self.state == 'missing':
            print('<missing>  {0}'.format(self.path(canonical)), file=sys.stderr)
        if self.state == 'bad':
            print('<outdated>  {0}'.format(self.path(canonical)), file=sys.stderr)

    def rehash(self, canonical=False):
        """Rehash the file and store the new checksum and timestamp"""
        self.ts = self.mtime
        newsum = hashfile(self.filename)
        if self.state == 'good' and newsum != self.shatag:
            print('<invalid>  {0}'.format(self.path(canonical)), file=sys.stderr)
        self.shatag = newsum
        self.write()
        self.state = 'good'

    def scrub(self, canonical=False):
        """Rehash the file and store new checksum and timestamp for uncorrupted
        files only and report about corrupted files"""
        self.ts = self.mtime
        newsum = hashfile(self.filename)
        if self.state == 'good' and newsum != self.shatag:
            print('<invalid>  {0}'.format(self.path(canonical)), file=sys.stderr)
            print(' tag says: '+self.shatag, file=sys.stderr)
            print(' got     : '+newsum, file=sys.stderr)
        else:
            self.shatag = newsum
            self.write()
            self.state = 'good'


def Store(url=None, name=None):
        """Factory to build a store. A store is an abstraction for a remote
	database to which one can send hashes, and quickly query for alternate
	locations with identical hashes.
        Arguments:
        url -- URL of the http store, or local filename of sqlite database
        name -- name of the local host when putting objets to store and to
                differentiate local from remote dupes.
        
        """
        if url is None:
            url = '{0}/.shatagdb'.format(os.environ['HOME'])
    
        if url.startswith("http://") or url.startswith("https://") or url.startswith("insecure-https://"):
            from shatag.store.http import HTTPStore
            return HTTPStore(url, name)
        if url.startswith("couchdb:"):
            from shatag.store.couchdb import CouchStore
            return CouchStore(url, name)
        elif url.startswith("pg:"):
            from shatag.store.pg import PgStore
            return PgStore(url, name)
        else:
            from shatag.store.sqlite import LocalStore
            return LocalStore(url, name)

class IStore(object):
    def __init__(self, url=None, name=None):
        if name is None:
            name = chost()            

        self.name = name
        self.url = url

    def put(self, file):
        """add a file to the store, using the local default name"""
        self.record(self.name, file.fullpath(), file.shatag)

    def puttree(self, base, files):
        """put a bunch of files to the store, clearing the base first"""
        self.clear(base)
        for f in files:
            self.put(f)

    def lookup(self, file, remotenames=None):
        """lookup a file for collisions, and returns a StoreResult object"""
        local = list()
        remote = list()

        if file.state != 'good':
            raise NoChecksum()

        for (name, path) in self.fetch(file.shatag):
        
            if ((remotenames is None and name != self.name) or
               (remotenames is not None and name in remotenames)):
                    remote.append((name,path))
            elif path != file.fullpath():
                local.append((name,path))

        return StoreResult(file, remote, local) 


class SQLStore(IStore):
    """A store backed by some sort of SQL database. It expects
    self.db to be a valid DB-API database, and
    self.cursor to be a valid DB-API cursor.
    """
    def __init__(self, url=None, name=None):
        super().__init__(url, name)


    def clear(self, base='/', name=None):
        if (name is None):
            name = self.name
        if base[-1] != '/':
            base = base + '/'
        self.cursor.execute('delete from contents where name = :name and substr(path,1,length(:base)) like :base', {'name': name, 'base': base})
        return self.cursor.rowcount
    
    def record(self, name, path, tag):
        self.cursor.execute('delete from contents where name = ? and path = ?', (name, path))
        self.cursor.execute('insert into contents(hash,name,path) values(?,?,?)', (tag, name, path))

    def fetch(self,hash):
        self.cursor.execute('select name,path from contents where hash=?', (hash,))
        return self.cursor

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

class StoreResult(object):
    def __init__(self,file,remote,local):
        self.file = file
        self.remote = remote
        self.local = local

        if self.local:
            self.status = 2
        elif self.remote:
            self.status = 1
        else:
            self.status = 0

    def pretty(self):
        prefix = '\x1b[33;1m- '
        # sha256 of empty file
        if self.file.shatag == 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855':
            prefix = '\x1b[35;1m* ' 
        elif self.status == 2:
            prefix = '\x1b[31;1m+ '
        elif self.status == 1:
            prefix = '\x1b[32;1m= '

        return prefix + self.file.filename + "\x1b[0m"

class Config(object):
    def __init__(self):

        self.database = None
        self.name = None
        # Change this if on windows
        self.backend = 'xattr'

        try:
            with open('{0}/.shatagrc'.format(os.environ['HOME']),'r') as f:
                d = yaml.load(f)
                if 'database' in d:
                    self.database = d['database']
                if 'name' in d:
                    self.name = d['name']
                if 'backend' in d:
                    self.backend = d['backend']
        except IOError as e:
            pass

      
def backend(name):
    """Create a Backend object. A backend is an abstraction for local tag storage, that may
    not have a fast way of locating files with identical hashes, but is fast to query on a per-file basis.
    """
    return __import__('shatag.backend.{0}'.format(name), globals(), locals(), ['Backend'], 0).Backend()


#!/usr/bin/env python
#
# Copyright (c) 2018 SAP SE
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# this script checks for volume attachments of already deleted volumes in the cinder db

import argparse
import sys
import ConfigParser
import logging
import datetime

from prettytable import PrettyTable
from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import MetaData
from sqlalchemy import select
from sqlalchemy import join
from sqlalchemy import Table
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.expression import false
from sqlalchemy.ext.declarative import declarative_base

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)-15s %(message)s')


# get all the volumes in state "error_deleting"
def get_error_deleting_volumes(meta):

    error_deleting_volumes = []

    volumes_t = Table('volumes', meta, autoload=True)
    error_deleting_volumes_q = select(columns=[volumes_t.c.id]).where(volumes_t.c.status == "error_deleting")

    # convert the query result into a list
    for i in error_deleting_volumes_q.execute():
        error_deleting_volumes.append(i[0])

    return error_deleting_volumes

# delete all the volumes in state "error_deleting"
def fix_error_deleting_volumes(meta, error_deleting_volumes):

    volumes_t = Table('volumes', meta, autoload=True)
    volume_attachment_t = Table('volume_attachment', meta, autoload=True)
    volume_metadata_t = Table('volume_metadata', meta, autoload=True)
    volume_admin_metadata_t = Table('volume_admin_metadata', meta, autoload=True)

    for error_deleting_volumes_id in error_deleting_volumes:
        log.info ("-- deleting possible volume admin metadata for volume id: %s", error_deleting_volumes_id)
        delete_volume_admin_metadata_q = volume_admin_metadata_t.delete().where(volume_admin_metadata_t.c.volume_id == error_deleting_volumes_id)
        delete_volume_admin_metadata_q.execute()
        log.info ("-- deleting possible volume metadata for volume id: %s", error_deleting_volumes_id)
        delete_volume_metadata_q = volume_metadata_t.delete().where(volume_metadata_t.c.volume_id == error_deleting_volumes_id)
        delete_volume_metadata_q.execute()
        log.info("-- deleting possible volume attachments for volume id: %s", error_deleting_volumes_id)
        delete_volume_attachment_q = volume_attachment_t.delete().where(volume_attachment_t.c.volume_id == error_deleting_volumes_id)
        delete_volume_attachment_q.execute()
        log.info ("-- deleting volume id: %s", error_deleting_volumes_id)
        delete_volume_q = volumes_t.delete().where(volumes_t.c.id == error_deleting_volumes_id)
        delete_volume_q.execute()

# get all the snapshots in state "error_deleting"
def get_error_deleting_snapshots(meta):

    error_deleting_snapshots = []

    snapshots_t = Table('snapshots', meta, autoload=True)
    error_deleting_snapshots_q = select(columns=[snapshots_t.c.id]).where(snapshots_t.c.status == "error_deleting")

    # convert the query result into a list
    for i in error_deleting_snapshots_q.execute():
        error_deleting_snapshots.append(i[0])

    return error_deleting_snapshots

# delete all the snapshots in state "error_deleting"
def fix_error_deleting_snapshots(meta, error_deleting_snapshots):

    snapshots_t = Table('snapshots', meta, autoload=True)

    for error_deleting_snapshots_id in error_deleting_snapshots:
        log.info ("-- deleting snapshot id: %s", error_deleting_snapshots_id)
        delete_snapshot_q = snapshots_t.delete().where(snapshots_t.c.id == error_deleting_snapshots_id)
        delete_snapshot_q.execute()

# get all the rows with a volume attachment still defined where corresponding the volume is already deleted
def get_wrong_volume_attachments(meta):

    wrong_attachments = {}
    volume_attachment_t = Table('volume_attachment', meta, autoload=True)
    volumes_t = Table('volumes', meta, autoload=True)
    attachment_join = volume_attachment_t.join(volumes_t,volume_attachment_t.c.volume_id == volumes_t.c.id)
    wrong_volume_attachment_q = select(columns=[volumes_t.c.id,volumes_t.c.deleted,volume_attachment_t.c.id,volume_attachment_t.c.deleted]).select_from(attachment_join).where(and_(volumes_t.c.deleted == "true",volume_attachment_t.c.deleted == "false"))

    # return a dict indexed by volume_attachment_id and with the value volume_id for non deleted volume_attachments
    for (volume_id, volume_deleted, volume_attachment_id, volume_attachment_deleted) in wrong_volume_attachment_q.execute():
        wrong_attachments[volume_attachment_id] = volume_id
    return wrong_attachments

# delete volume attachment still defined where corresponding the volume is already deleted
def fix_wrong_volume_attachments(meta, wrong_attachments):

    volume_attachment_t = Table('volume_attachment', meta, autoload=True)

    for volume_attachment_id in wrong_attachments:
        log.info ("-- deleting volume attachment id: %s", volume_attachment_id)
        delete_volume_attachment_q = volume_attachment_t.delete().where(volume_attachment_t.c.id == volume_attachment_id)
        delete_volume_attachment_q.execute()

# get all the rows, which have the deleted flag set, but not the delete_at column
def get_missing_deleted_at(meta, table_names):

    missing_deleted_at = {}
    for t in table_names:
        a_table_t = Table(t, meta, autoload=True)
        a_table_select_deleted_at_q = a_table_t.select().where(
            and_(a_table_t.c.deleted == True, a_table_t.c.deleted_at == None))

        for row in a_table_select_deleted_at_q.execute():
            missing_deleted_at[row.id] = t
    return missing_deleted_at

# set deleted_at to updated_at value if not set for marked as deleted rows
def fix_missing_deleted_at(meta, table_names):
    now = datetime.datetime.utcnow()
    for t in table_names:
        a_table_t = Table(t, meta, autoload=True)

        log.info("- fixing columns with missing deleted_at times in the %s table", t)
        a_table_set_deleted_at_q = a_table_t.update().where(
            and_(a_table_t.c.deleted == True, a_atable_t.c.deleted_at == None)).values(
            deleted_at=now)
        a_table_set_deleted_at_q.execute()

# establish a database connection and return the handle
def makeConnection(db_url):

    engine = create_engine(db_url)
    engine.connect()
    Session = sessionmaker(bind=engine)
    thisSession = Session()
    metadata = MetaData()
    metadata.bind = engine
    Base = declarative_base()
    return thisSession, metadata, Base

# return the database connection string from the config file
def get_db_url(config_file):

    parser = ConfigParser.SafeConfigParser()
    try:
        parser.read(config_file)
        db_url = parser.get('database', 'connection', raw=True)
    except:
        log.info("ERROR: Check Cinder configuration file.")
        sys.exit(2)
    return db_url

# cmdline handling
def parse_cmdline_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config",
                        default='./cinder.conf',
                        help='configuration file')
    parser.add_argument("--dry-run",
                       action="store_true",
                       help='print only what would be done without actually doing it')
    return parser.parse_args()

def main():
    try:
        args = parse_cmdline_args()
    except Exception as e:
        log.error("Check command line arguments (%s)", e.strerror)

    # connect to the DB
    db_url = get_db_url(args.config)
    cinder_session, cinder_metadata, cinder_Base = makeConnection(db_url)

    # fixing possible volumes in state "error-deleting"
    error_deleting_volumes = get_error_deleting_volumes(cinder_metadata)
    if len(error_deleting_volumes) != 0:
        log.info("- volumes in state error_deleting found")
        # print out what we would delete
        for error_deleting_volumes_id in error_deleting_volumes:
            log.info("-- volume id: %s", error_deleting_volumes_id)
        if not args.dry_run:
            log.info("- deleting volumes in state error_deleting")
            fix_error_deleting_volumes(cinder_metadata, error_deleting_volumes)
    else:
        log.info("- no volumes in state error_deleting found")

    # fixing possible snapshots in state "error-deleting"
    error_deleting_snapshots = get_error_deleting_snapshots(cinder_metadata)
    if len(error_deleting_snapshots) != 0:
        log.info("- snapshots in state error_deleting found")
        # print out what we would delete
        for error_deleting_snapshots_id in error_deleting_snapshots:
            log.info("-- snapshot id: %s", error_deleting_snapshots_id)
        if not args.dry_run:
            log.info("- deleting snapshots in state error_deleting")
            fix_error_deleting_snapshots(cinder_metadata, error_deleting_snapshots)
    else:
        log.info("- no snapshots in state error_deleting found")

    # fixing possible wrong attachment entries
    wrong_attachments = get_wrong_volume_attachments(cinder_metadata)
    if len(wrong_attachments) != 0:
        log.info("- volume attachment inconsistencies found")
        # print out what we would delete
        for volume_attachment_id in wrong_attachments:
            log.info("-- volume attachment id: %s - deleted volume id: %s", volume_attachment_id, wrong_attachments[volume_attachment_id])
        if not args.dry_run:
            log.info("- removing volume attachment inconsistencies found")
            fix_wrong_volume_attachments(cinder_metadata, wrong_attachments)
    else:
        log.info("- volume attachments are consistent")

    # fixing possible missing deleted_at timestamps in some tables
    # tables which sometimes have missing deleted_at values
    # TODO: maybe this can be generated automatically as a list of tables with deleted_at column
    table_names = [ 'snapshots', 'volume_attachment' ]

    missing_deleted_at = get_missing_deleted_at(cinder_metadata, table_names)
    if len(missing_deleted_at) != 0:
        log.info("- missing deleted_at values found:")
        # print out what we would delete
        for missing_deleted_at_id in missing_deleted_at:
            log.info("--- id %s of the %s table is missing deleted_at time", missing_deleted_at_id, missing_deleted_at[missing_deleted_at_id])
        if not args.dry_run:
            log.info("- setting missing deleted_at values")
            fix_missing_deleted_at(cinder_metadata, table_names)
    else:
        log.info("- no missing deleted_at values")

if __name__ == "__main__":
    main()

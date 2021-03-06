#!/usr/bin/env python

from neo4jrestclient.client import GraphDatabase
import requests
import argparse
from datetime import datetime
import pickle
from csv import DictReader
from lxml import etree
import urlparse
from ijson import items
import urllib
from collections import namedtuple
StandardsRelationship = namedtuple("StandardsRelationship", ['standard', 'relation'])
INDEX_NAME = 'resources'
xml_namespaces = {
                  "dc": "http://purl.org/dc/elements/1.1/",
                  "dct": "http://purl.org/dc/terms/",
                  "ieee": "http://www.ieee.org/xsd/LOMv1p0",
                  "nsdl_dc": "http://ns.nsdl.org/nsdl_dc_v1.02/",
                  "xsi": "http://www.w3.org/2001/XMLSchema-instance",
                  "oa": "http://www.openarchives.org/OAI/2.0/"
                 }


def save_resource_node(envelope, db, idx):
    try:
        found_items = idx.query('resource:' + urllib.quote_plus(envelope['resource_locator']))
    except Exception:
        found_items = []
    if  len(found_items) > 0:
        new_node = found_items[0]
    else:
        print "Created Content Node"
        new_node = db.nodes.create(resource=envelope['resource_locator'])
        idx['resource'][urllib.quote_plus(envelope['resource_locator'])] = new_node
    print new_node.url
    return new_node


def get_conforms_to_data(envelope):
    xml = etree.fromstring(envelope['resource_data'])
    return (StandardsRelationship(standard=urllib.quote_plus(x.text), relation='conformsTo') for x in xml.xpath("./dct:conformsTo", namespaces=xml_namespaces))


def get_paradata_standards_data(envelope):
    para = envelope['resource_data']['activity']
    for related in para['related']:
        if related['objectType'].lower() == 'academic standard':
            yield StandardsRelationship(standard=urllib.quote_plus(related['id']), relation=urllib.quote_plus(para['verb']['action']))


def process_conforms_to_data(conforms_to, db, idx, new_node):
    for i in conforms_to:
        try:
            found_standard = idx.query(':'.join(['standard', i.standard]))
        except:
            found_standard = []
        if  len(found_standard) > 0:
            cc_node = found_standard[0]
            cc_node.properties['standard'] = i.standard
            cc_node.update()
        else:
            print "Created CC Node"
            cc_node = db.nodes.create(standard=i.standard)
            idx['standard'][i.standard] = cc_node
        new_node.relationships.create(i.relation, cc_node)


def save_data(data_set, db, idx, get_conforms_to_data_from_envelope):
    for envelope in data_set:
        new_node = save_resource_node(envelope, db, idx)
        process_conforms_to_data(get_conforms_to_data_from_envelope(envelope), db, idx, new_node)


def parse_standards_data(data, prefix, valid_ids):
    for item in data:
        if 'leaf' in item and item['leaf'] and prefix + '.' + item['asn_statementNotation'] in valid_ids and 'asn_identifier' in item:
            return (item['asn_identifier'], prefix + '.' + item['asn_statementNotation'])
        if 'children' in item:
            return parse_standards_data(item['children'], prefix, valid_ids)


def import_cc_state(url, prefix, valid_ids):
    cc_info = []
    data = requests.get(url).json
    for item in data:
        cc_id = parse_standards_data(item['children'], prefix, valid_ids)
        if cc_id is not None:
            cc_info.append(cc_id)
    return cc_info


def process_cc_standards(db, idx):
    ids = set()

    def test_standard(standard):
        query = ':'.join(["standard", urllib.quote_plus(standard)])
        result = idx.query(query)
        if len(result) > 0:
            return result[0]
        else:
            print "Created Standard Node"
            print query[9:] == urllib.quote_plus(standard)
            node = db.nodes.create(standard=standard)
            idx['standard'][urllib.quote_plus(standard)] = dot_node
            return node
    with open('E0330_ccss_identifiers.csv', 'rU') as f:
        dr = DictReader(f)
        for row in dr:
            dot_node = test_standard(row['Dot notation'])
            url_node = test_standard(row['URI'])
            uuid_node = test_standard(row['GUID'])
            url_node.sameAs(dot_node)
            uuid_node.sameAs(dot_node)
            ids.add(row['Dot notation'])
    return ids


def process_purl_data(db, idx, urls, ids):
    for url in urls:
        results = import_cc_state(url[1], url[0], ids)
        for result in results:
            asn_node_query = idx.query('standard:' + urllib.quote_plus(result[0]))
            cc_node_query = idx.query('standard:' + urllib.quote_plus(result[1]))
            if len(asn_node_query) > 0 and len(cc_node_query) > 0:
                asn_node = asn_node_query[0]
                cc_node = cc_node_query[0]
                asn_node.sameAs(cc_node)


def init_neo4j(url):
    db = GraphDatabase(url)
    if INDEX_NAME in db.nodes.indexes:
        idx = db.nodes.indexes.get(INDEX_NAME)
    else:
        idx = db.nodes.indexes.create(INDEX_NAME, type="fulltext")
    return db, idx


def process_data_service(results, db, idx, conforms_func):
    for result_item in results:
        save_data(result_item['resource_data'], db, idx, conforms_func)


def load_date():
    try:
        with open('date', 'r') as f:
            start = pickle.load(f)
    except:
        start = None
    until = datetime.utcnow()
    with open('date', 'w') as f:
        pickle.dump(until, f)
    return start, until


def load_from_dataservice(url, start, until):
    parts = urlparse.urlparse(url)
    query = {}
    if start is not None:
        query['from'] = start.isoformat()
    if until is not None:
        query['until'] = until.isoformat()
    parts = urlparse.ParseResult(scheme=parts.scheme,
                                 netloc=parts.netloc,
                                 path=parts.path,
                                 params=parts.params,
                                 query=urllib.urlencode(query),
                                 fragment=parts.fragment)
    url = urlparse.urlunparse(parts)
    results = requests.get(url)
    results = items(results.raw, 'documents.item')
    return results


def main(args):
    start, until = load_date()
    urls = [('Literacy', 'http://asn.jesandco.org/resources/D10003FC_manifest.json'),
            ("Math", "http://asn.jesandco.org/resources/D10003FB_manifest.json")]
    whitelist = ['matched', 'recommended', 'aligned']
    db, idx = init_neo4j(args.db)
    results = load_from_dataservice(args.url, start, until)
    process_data_service(results, db, idx, get_conforms_to_data)

    def filter_paradata(item):
        valid = True
        for x in item['resource_data']:
            valid = valid or x['resource_data']['verb']['action'] in whitelist
        return valid
    results = (x for x in load_from_dataservice(args.para, start, until) if filter_paradata(x))
    process_data_service(results, db, idx, get_paradata_standards_data)
    ids = process_cc_standards(db, idx)
    process_purl_data(db, idx, urls, ids)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import LR data into Neo4j")
    parser.add_argument("--url", dest="url", default='https://node01.public.learningregistry.net/extract/standards-alignment-dct-conformsTo/resource-by-ts', help="URL to the data service to harvest from")
    parser.add_argument("--para", dest="para", default='https://node01.public.learningregistry.net/extract/standards-alignment-lr-paradata/resource-by-ts', help="URL to the data service to harvest from")
    parser.add_argument("--db", dest="db", default="http://localhost:7474/db/data/", help="URL to neo4j database")
    args = parser.parse_args()
    main(args)

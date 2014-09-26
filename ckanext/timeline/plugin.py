from __future__ import absolute_import, with_statement, print_function, generators, nested_scopes, division
# from __future__ import unicode_literals  # Causes template error

import logging
import threading
import multiprocessing
from urllib2 import urlopen

import ckan.plugins as plugins
import ckan.logic
import ckan.lib.search
import ckan.lib.search.query
import ckan.plugins.toolkit as toolkit
from ckan.common import _, c

log = logging.getLogger(__name__)

HOST = 'http://localhost:8983/solr'
QUERY = 'extras_TempCoverageBegin:[* TO {e}] AND extras_TempCoverageEnd:[{s} TO *]'
START_FIELD = 'extras_TempCoverageBegin'
END_FIELD = 'extras_TempCoverageEnd'
RANGES = 100


class TimelineAPIPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.interfaces.IActions, inherit=True)
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IPackageController, inherit=True)

    def update_config(self, config):
        toolkit.add_template_directory(config, 'templates')
        toolkit.add_resource('fanstatic', 'ckanext-timeline')

    def before_search(self, search_params):
        extras = search_params.get('extras')
        if not extras:
            # There are no extras in the search params, so do nothing.
            return search_params

        start_point = extras.get('ext_timeline_start')
        end_point = extras.get('ext_timeline_end')

        if not start_point and not end_point:
            # The user didn't select either a start and/or end date, so do nothing.
            return search_params
        if not start_point:
            start_point = '*'
        if not end_point:
            end_point = '*'

        # Add a time-range query with the selected start and/or end points into the Solr facet queries.
        fq = search_params['fq']
        fq = '{fq} +{q}'.format(fq=fq, q=QUERY).format(s=start_point, e=end_point)

        search_params['fq'] = fq

        return search_params

    def after_search(self, search_results, search_params):
        '''
        Exports Solr 'q' and 'fq' to the context so the timeline can use them
        '''

        c.timeline_q = search_params.get('q', '')
        c.timeline_fq = search_params.get('fq', '')

        return search_results

    def get_actions(self):
        return {'timeline': timeline}

@ckan.logic.side_effect_free
def timeline(context, request_data):
    '''
    Return a list of points for a timeline plot

    :param start: the start point in time
    :type start: int
    :param end: the end point in time
    :type end: int
    :param method: the way to execute the queries
    :type method: str

    :rtype: list
    '''

    #ckan.logic.check_access('timeline', context, request_data)

    start = request_data.get('start')
    end = request_data.get('end')
    method = request_data.get('method', 't')

    # Validate values
    if start is None:
        raise ckan.logic.ValidationError({'start': _('Missing value')})
    if end is None:
        raise ckan.logic.ValidationError({'end': _('Missing value')})
    if method not in ('s', 'p', 't'):
        raise ckan.logic.ValidationError({'method': _('Wrong value')})

    # Handle open/'*' start and end points
    if start == '*':
        try:
            con = ckan.lib.search.make_connection()
            start = con.query('*:*',
                              fields=['id', '{f}'.format(f=START_FIELD)],
                              sort=['{f} asc'.format(f=START_FIELD)],
                              rows=1).results[0][START_FIELD]
        except:
            raise ckan.logic.ValidationError({'start': _('Could not find start value from Solr')})
        finally:
            con.close()
    if end == '*':
        try:
            con = ckan.lib.search.make_connection()
            end = con.query('*:*',
                            fields=['id', '{f}'.format(f=END_FIELD)],
                            sort=['{f} desc'.format(f=END_FIELD)],
                            rows=1).results[0][END_FIELD]
        except:
            raise ckan.logic.ValidationError({'end': _('Could not find end value from Solr')})
        finally:
            con.close()

    # Convert to ints
    start = int(start)
    end = int(end)

    # Verify 'end' larger than 'start'
    if end <= start:
        raise ckan.logic.ValidationError({'end': _('Smaller or equal to start')})

    delta = end - start

    interval = delta / RANGES

    # Expand amount of ranges to RANGES
    if interval < 1:
        interval = 1.0
        start -= (RANGES - delta) // 2

    # Use a set for tuple uniqueness
    ls = set()

    # Create the ranges
    for a in range(RANGES):
        s = int(start + interval * a)
        e = int(start + interval * (a + 1))
        m = (s + e) // 2

        # Make sure 's' and 'e' are not equal
        if s != e:
            ls.add((s, e, m))

    if len(ls) != RANGES:
        log.warning('{l} not {r} elements'.format(l=len(ls), r=RANGES))

    # Convert 'ls' to a list, because of JSON
    ls = list(ls)
    # log.debug('ls: {l}'.format(l=ls))

    # Make requests
    if method == 't':
        # TODO: Would collections.deque be faster and/or thread-safer?
        rl = []
        t = [threading.Thread(target=lambda st, en, md: rl.append(ps((st, en, md))), args=l) for l in ls]
        [x.start() for x in t]
        [x.join() for x in t]
    elif method == 'p':
        rl = multiprocessing.Pool(multiprocessing.cpu_count()).map(ps, ls)
    elif method == 's':
        rl = [ps(l) for l in ls]

    # Sort the list for readability
    return sorted(rl)


def ps(t):
    '''
    Makes a request to Solr and returns the result

    :param t: Tuple containing "start", "end" and "mean" values
    :type t: (int, int, int)
    :rtype: (int, int, int, int)
    '''
    s, e, m = t
    solr = ckan.lib.search.make_connection()
    n = solr.query(QUERY.format(s=s, e=e),
                   fields=['id'],
                   rows=0)
    solr.close()
    return s, e, m, n._numFound

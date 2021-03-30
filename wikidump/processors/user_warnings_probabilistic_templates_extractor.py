"""Extract the user warning templates by searching the salient words which characterizes the template"""

import collections
import json
import more_itertools
import mwxml
import pathlib
import datetime
import bz2
import gzip
from typing import Iterable, Iterator, Mapping, NamedTuple, Optional
from backports.datetime_fromisoformat import MonkeyPatch
import ahocorasick

from .. import dumper, extractors, utils

# Polyfiller for retrocompatibiliy with Python3.5
MonkeyPatch.patch_fromisoformat()

# REVISION AND PAGE CLASSES
class Revision:
    """Class which represent a revision of the user talk page"""
    def __init__(self, id: str, user: mwxml.Revision.User, timestamp: str, templates: Iterable[extractors.user_warnings_probabilistic_subst.UserWarningTokens]):
        self.id = id                                                # revision id
        self.user = user                                            # revision user
        self.timestamp = timestamp                                  # revision timestamp
        self.templates = templates                                  # list of probable user warning templates in that user talk page

    def to_dict(self) -> str:
        """Converts the object instance into a dictionary"""
        obj = dict()
        obj['id'] = self.id
        user_id = ''
        user_name = ''
        if self.user:
            user_id = self.user.id
            user_name = self.user.text
        obj['user_id'] = user_id
        obj['user_name'] = user_name
        obj['timestamp'] = self.timestamp
        obj['templates'] = list()
        for temp in self.templates:
            obj['templates'].append(temp.to_dict())
        return obj

    def __repr__(self):
        return 'date: {}'.format(self.timestamp)

class Page:
    """Class which represent a page containing a list of revisions"""
    def __init__(self, id: str, namespace: str, title: str, revisions: Iterator[Revision]):
        self.id = id                            # page id
        self.namespace = namespace              # page namespace
        self.title = title                      # page title
        self.revisions = revisions              # list of revisions

    def to_dict(self) -> Mapping:
        """Converts the object instance into a dictionary"""
        obj = dict()
        obj['id'] = self.id
        obj['namespace'] = self.namespace
        obj['title'] = self.title
        obj['revisions'] = list()
        for rev in self.revisions:
            obj['revisions'].append(rev.to_dict())
        return obj

# TODO for now not in 7z
def input_reader(path: str):
    compression = pathlib.Path(path).suffix
    """Open a compressed file, if it is compressed"""
    if compression == 'bz2':
        return bz2.open(path, 'rt')
    elif compression == 'gzip' or compression == '.gz':
        return gzip.open(path, 'rt')
    else:
        return open(path, 'rt')

# todo to delete if there's a better way
def extract_templates_words(files: Iterable[pathlib.Path]) -> Mapping:
    """Returns a dictionary where the key is the name of the template and the value is a list of list of words which characterize that template the most and the revision timestamp"""
    template_dictionary = dict()
    utils.log("Preparing the templates dictionary..")
    for file_name in files:
        file = input_reader(file_name)
        try:   # todo remove the try catch
            for line in file:
                template_page = json.loads(line)
                template_dictionary[template_page['title']] = list() # key = name of the template
                for rev in template_page['revisions']: # for each revision
                    template_dictionary[template_page['title']].append((rev['words_to_search'], rev['timestamp']))    # concatenate each words to find (list of lists)
        except:
            pass
        file.close()
    return template_dictionary

def build_trie(template_dictionary: Mapping) -> ahocorasick.Automaton:
    """Function which builds the trie from the previous mapping"""
    trie = ahocorasick.Automaton()
    word_set = set()
    with open('words_to_seach.txt', 'w') as f:
        for template in template_dictionary:
            for words_list,_ in template_dictionary[template]:
                for word in words_list:
                    trie.add_word(word, (template, word))   # key is the word to search, value is the template
                    word_set.add(word)
        for word in word_set:
            f.write(word + '\n')
        trie.make_automaton()
    return trie

def extract_revisions(
        mw_page: mwxml.Page,
        stats: Mapping,
        only_last_revision: bool,
        only_pages_with_user_warnings: bool,
        only_revisions_with_user_warnings: bool,
        template_trie: ahocorasick.Automaton(),
        templates_dictionary: Mapping,
        language: str) -> Iterator[Revision]:
    
    """Extracts the possible user warnings within a user talk page revision."""
    revisions = more_itertools.peekable(mw_page)

    # Newest revisions, useful only if the only_last_revision flag is set equal to true
    newest_revision = None

    for mw_revision in revisions:
        utils.dot()

        # check if it's last revision
        is_last_revision = not utils.has_next(revisions)

        # remove html comments
        text = utils.remove_comments(mw_revision.text or '')

        # entract the found templates
        templates = extractors.user_warnings_probabilistic_subst.extract_probabilistic_user_warning_templates(
            text, 
            language,
            mw_revision.timestamp.to_json(),
            template_trie, 
            templates_dictionary
        )

        # Build the revision
        rev = Revision(
            id=mw_revision.id,
            user=mw_revision.user,
            timestamp=mw_revision.timestamp.to_json(),
            templates=templates,
        )

        # Check the oldest revisions possible
        if not newest_revision:
            newest_revision = rev
        else:
            newest_date = datetime.datetime.fromisoformat(newest_revision.timestamp.replace('Z', '+00:00'))
            current_date = datetime.datetime.fromisoformat(mw_revision.timestamp.to_json().replace('Z', '+00:00'))
            # change the revision if the current one is newer
            if newest_date < current_date:
                newest_revision = rev

        # Update stats
        stats['performance']['revisions_analyzed'] += 1

        # requested only the last revision
        if only_last_revision:
            # asked for revisions with wikibreaks
            if only_revisions_with_user_warnings:
                # has the wikibreaks list not empty
                if newest_revision.templates and is_last_revision:
                    yield newest_revision
            elif is_last_revision:
                yield newest_revision
        elif only_revisions_with_user_warnings:
            if templates:
                yield rev
        else:
            yield rev

def extract_pages(
        dump: Iterable[mwxml.Page],
        stats: Mapping,
        only_last_revision: bool,
        only_pages_with_user_warnings: bool,
        only_revisions_with_user_warnings: bool,
        template_trie: ahocorasick.Automaton(),
        templates_dictionary: Mapping,
        language: str) -> Iterator[Page]:
    """Extract the probable templates within a user talk page using templates_dictionary."""

    # Loop on all the pages in the dump, one at a time
    for mw_page in dump:
        utils.log("Processing", mw_page.title)
        
        # Skip non-user talk page, according to https://en.wikipedia.org/wiki/Wikipedia:Namespace
        if mw_page.namespace != 3:
            utils.log('Skipped (namespace != 3)')
            continue

        revisions_generator = extract_revisions(
            mw_page,
            stats=stats,
            only_last_revision=only_last_revision,
            only_pages_with_user_warnings=only_pages_with_user_warnings,
            only_revisions_with_user_warnings=only_revisions_with_user_warnings,
            template_trie=template_trie,
            templates_dictionary=templates_dictionary,
            language=language
        )

        revisions_list = list(revisions_generator)

        # dictionary which checks the existance of certain templates
        to_add = dict()
        n_occurr = 0
        for rev in revisions_list:
            for temp in rev.templates:
                to_add[temp.name] = True
                n_occurr += 1

        page = Page(
            id=mw_page.id,
            namespace=mw_page.namespace,
            title=mw_page.title,
            revisions=revisions_list,
        )

        for key in to_add:
            if not key in stats['user_warnings_stats']['template_recognized']:
                stats['user_warnings_stats']['template_recognized'][key] = 0
            stats['user_warnings_stats']['template_recognized'][key] += 1

        # Return only the pages with at least one wikibreak if the flag's active
        if only_pages_with_user_warnings:
            if n_occurr > 0:
                stats['user_warnings_stats']['total'] += 1
                yield page
        else:
            if n_occurr > 0:
                stats['user_warnings_stats']['total'] += 1
            yield page
    
        if stats['user_warnings_stats']['total'] > 0:
            break

        stats['performance']['pages_analyzed'] += 1

def configure_subparsers(subparsers):
    """Configure a new subparser for the known languages."""
    parser = subparsers.add_parser(
        'extract-user-warnings-templates-probabilistic',
        help='Extract the possible templates substituted in a user talk page',
    )
    parser.add_argument(
        'tokens',
        metavar='FILE',
        type=pathlib.Path,
        nargs='+',
        help='Output of the extract-user-warnings-templates-tokens wikidump function. It accepts 7z or bzip2.',
    )
    parser.add_argument(
        '--only-pages-with-user-warnings',
        action='store_true',
        help='Consider only the pages with at least a revision which contains a possible user warning.',
    )
    parser.add_argument(
        '--only-revisions-with-user-warnings',
        action='store_true',
        help='Consider only the revisions with contain at least a possible user warning.',
    )
    parser.add_argument(
        '--only-last-revision',
        action='store_true',
        help='Consider only the last revision for each page.',
    )
    parser.add_argument(
        '--language',
        choices={'italian', 'catalan', 'spanish', 'english'},
        required=True,
        help='Language of the analyzed dump',
    )
    parser.set_defaults(func=main)


def main(
        dump: Iterable[mwxml.Page],
        features_output_h,
        stats_output_h,
        args) -> None:
    """Main function that parses the arguments and writes the output."""

    stats = {
        'performance': {
            'start_time': None,
            'end_time': None,
            'revisions_analyzed': 0,
            'pages_analyzed': 0,
        },
        'user_warnings_stats': {
            'total': 0, # total users who have at least a user warnigns substituted in their talk page
            'template_recognized': dict()   # templates and its usage
        }
    }
    
    # dictionary which stores the words which needs to be searched in order to establish if a certain template has been substituted there
    templates_dictionary = extract_templates_words(
        files=args.tokens,
    )

    # build a trie
    template_trie = build_trie(templates_dictionary)

    pages_generator = extract_pages(
        dump,
        stats=stats,
        only_last_revision=args.only_last_revision,
        only_pages_with_user_warnings=args.only_pages_with_user_warnings,
        only_revisions_with_user_warnings=args.only_revisions_with_user_warnings,
        template_trie=template_trie,
        templates_dictionary=templates_dictionary,
        language=args.language
    )

    stats['performance']['start_time'] = datetime.datetime.utcnow()

    for obj in pages_generator:
        features_output_h.write(json.dumps(obj.to_dict(), indent=4))
        features_output_h.write("\n")
    
    stats['performance']['end_time'] = datetime.datetime.utcnow()
    stats_output_h.write(json.dumps(stats, indent=4, default=str))
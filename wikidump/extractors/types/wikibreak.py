from typing import Mapping, Iterable

class Wikibreak:
    """Class which stores the possible attributes, if any, of a Wikipause object"""

    """The set of relevants attribute of the wikibreak"""
    def __init__(self, wikipause_name: str, options: Mapping):
        self.wikipause_name = wikipause_name.strip().lower()
        self.options = options

    def to_dict(self) -> Mapping:
        """It converts the wikibreak class instance into a dictionary"""
        obj = dict()
        obj['wikibreak_name'] = self.wikipause_name
        obj['options'] = self.options   # TODO see if it works
        return obj

    def __repr__(self) -> str:
        return 'wikipause_name: {}; options {}'.format(
            self.wikipause_name, self.options)
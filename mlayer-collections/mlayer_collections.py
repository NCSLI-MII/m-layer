#! /usr/bin/env python3
# vim:fenc=utf-8
#
# Copyright © 2025 Ryan Mackenzie White <ryan.white4@canada.ca>
#
# Distributed under terms of the Copyright © Her Majesty the Queen in Right of Canada, as represented by the Minister of Statistics Canada, 2019. license.

"""
Utility to obtain mlayer from api
"""
import json
import requests


class MLayerCollections:

    def __init__(self, doapi=True):
        # self._path_root = get_project_root()
        self._api = "https://api.mlayer.org"
        self._doapi = doapi
        self._output = '/tmp/mlayer'

        # Ordered list of transform to run
        # Defines the loading order needed to establish object relations
        # See getCollections
        self._transform = {
                'systems': self._transformSystem,
                'prefixes': self._transformPrefix,
                'dimensions': self._transformDimension,
                'functions': self._transformFunction,
                'aspects': self._transformAspect,
                'units': self._transformUnit,
                'scales': self._transformScale,
                'scaletypes': self._transformScaleTypes,
                'conversions': self._transformConversions,
                'casts': self._transformCasts
                }

    def _transformPrefix(self):
        pass

    def _transformSystem(self):
        pass

    def _transformDimension(self):
        pass

    def _transformAspect(self):
        pass

    def _transformUnit(self):
        pass

    def _transformScale(self):
        pass

    def _transformFunction(self):
        pass

    def _transformScaleTypes(self):
        pass

    def _transformConversions(self):
        pass

    def _transformCasts(self):
        pass

    def _storeCollection(self, type_, lst):
        filename = f'{self._output}/{type_}.json'
        with open(filename, 'w') as json_file:
            json.dump(lst, json_file, indent=2)
        print(f'API response saved to {filename}')

    def getCollections(self):
        for collection in self._transform.keys():
            print(collection)
            self._getCollection(collection)

    def _getCollection(self, type_):
        if self._doapi is True:
            response = requests.get(f'{self._api}/{type_}')
            print(response.status_code)
            if response.status_code == 200:
                self._storeCollection(type_, response.json())

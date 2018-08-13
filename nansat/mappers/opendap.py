# Name:         opendap.py
# Purpose:      Abstract class Opendap is extended by some mappers
# Author:       Anton Korosov, Artem Moiseev
# Licence:      This file is part of NANSAT. You can redistribute it or modify
#               under the terms of GNU General Public License, v.3
#               http://www.gnu.org/licenses/gpl-3.0.html

# http://cfconventions.org/wkt-proj-4.html

from __future__ import unicode_literals, absolute_import, print_function
import os
import warnings
import numpy as np

try:
    from netCDF4 import Dataset
except ImportError:
    raise ImportError('''
         Cannot import Dataset from netCDF4.
         You cannot access OC CCI data but
         Nansat will work.''')

from nansat.vrt import VRT

from nansat.exceptions import WrongMapperError
import sys


if sys.version_info.major == 2:
    str_types = [str, unicode]
else:
    str_types = [str]


class Opendap(VRT):
    """Methods for all OpenDAP mappers"""

    P2S = {
        'H': 60*60,
        'D': 86400,
        'M': 30*24*60*60,
        'Y': 31536000,
        }

    # TODO:add band metadata

    def test_mapper(self, filename):
        """Tests if filename fits mapper. May raise WrongMapperError

            Parameters
            ----------
                filename: str
                    absolute url of input file

            Raises
            ------
                WrongMapperError: if input url does not match with list of
                    urls for a mapper
        """
        base_url_match = False
        # TODO: baseURLs var name should be changed here and in all mappers
        for base_url in self.baseURLs:
            if filename.startswith(base_url):
                base_url_match = True
                break
        if not base_url_match:
            raise WrongMapperError(filename)

    def get_dataset(self, ds):
        """Open Dataset

           Parameters
           ----------
                ds: str or netCDF4.Dataset
        """
        if ds is None:
            try:
                ds = Dataset(self.filename)
            except:
                raise ValueError('Cannot open %s' % self.filename)
        elif type(ds) != Dataset:
            raise ValueError('Input ds is not netCDF.Dataset!')

        return ds

    def get_geospatial_variable_names(self):
        """Get names of variables with both spatial dimensions"""
        ds_names = []

        for var in self.ds.variables:
            var_dimensions = self.ds.variables[var].dimensions
            # TODO: xName and yName var names should be changed here and in all mappers
            if self.xName in var_dimensions and self.yName in var_dimensions:
                ds_names.append(var)

        return ds_names

    def get_dataset_time(self):
        """Load data from time variable"""
        cachefile = ''
        if type(self.cachedir) in str_types and os.path.isdir(self.cachedir):
            # do caching
            cachefile = '%s_%s.npz' % (os.path.join(self.cachedir,
                                       os.path.split(self.filename)[1]),
                                       self.timeVarName)

        if os.path.exists(cachefile):
            ds_time = np.load(cachefile)[self.timeVarName]
        else:
            warnings.warn('Time consuming loading time from OpenDAP...')
            ds_time = self.ds.variables[self.timeVarName][:]
            warnings.warn('Loading time - OK!')

        if os.path.exists(cachefile):
            np.savez(cachefile, **{self.timeVarName: ds_time})

        return ds_time

    def get_layer_datetime(self, date, datetimes):
        """ Get datetime of the matching layer and layer number"""

        if len(datetimes) == 1 or date is None:
            layer_num = 0
        else:
            # find closest layer
            datetime_resolution = np.abs(datetimes[0] - datetimes[1])
            date = np.datetime64(date).astype('M8[s]')
            matching_date_diff = np.min(np.abs(datetimes - date))
            if matching_date_diff > datetime_resolution:
                raise ValueError('Date %s is out of range' % date)
            layer_num = np.argmin(np.abs(datetimes - date))

        layer_date = datetimes[layer_num]

        return layer_num, layer_date

    def get_metaitem(self, url, var_name, var_dimensions):
        """Set metadata for creating band VRT

           Parameters
           ----------
                url: str,
                    absolute url of an input file
                var_name: str,
                    name of a variable/band from netCDF file
                var_dimensions: iterable
                    iterable array with dimensions of the variable

            Returns
            -------
                meta_item: dict
                    dictionary with src and dst parameters for creating a gdal band
        """

        # assemble dimensions string
        dims = ''.join(['[%s]' % dim for dim in var_dimensions])
        meta_item = {
            'src': {'SourceFilename': '{url}?{var}.{var}{shape}'.format(url=url,
                                                                        var=var_name,
                                                                        shape=dims),
                    'SourceBand': 1},
            'dst': {'name': var_name,
                    'dataType': 6}
        }

        for attr in self.ds.variables[var_name].ncattrs():
            attr_key = attr.encode('ascii', 'ignore')
            attr_val = self.ds.variables[var_name].getncattr(attr)
            if type(attr_val) in str_types:
                attr_val = attr_val.encode('ascii', 'ignore')
            if attr_key in ['scale', 'scale_factor']:
                meta_item['src']['ScaleRatio'] = attr_val
            elif attr_key in ['offset', 'add_offset']:
                meta_item['src']['ScaleOffset'] = attr_val
            else:
                meta_item['dst'][attr_key] = str(attr_val)

        return meta_item

    def create_vrt(self, filename, gdalDataset, gdalMetadata, date, ds, bands, cachedir):
        """ Create VRT

            Parameters
            ----------
                filename: str,
                    absolute url of an input file
                date: str,
                    date in format YYYY-MM-DD
                ds: netDCF.Dataset
                bands: list
                    list of src bands
                cachedir: str
        """
        if date is None:
            warnings.warn('Date is not specified! Will return the first layer. '
                          'Please add date="YYYY-MM-DD"')

        self.filename = filename
        self.cachedir = cachedir
        self.ds = self.get_dataset(ds)

        ds_time = self.get_dataset_time()
        ds_times = self.convert_dstime_datetimes(ds_time)
        layer_time_id, layer_date = self.get_layer_datetime(date, ds_times)

        if bands is None:
            var_names = self.get_geospatial_variable_names()
        else:
            var_names = bands

        # create VRT with correct lon/lat (geotransform)
        raster_x, raster_y = self.get_shape()
        geotransform = self.get_geotransform()
        self._init_from_dataset_params(int(raster_x), int(raster_y),
                                       geotransform, self.srcDSProjection)

        meta_dict = []
        for var_name in var_names:
            # Get list of dimensions for a variable
            var_dimensions = list(self.ds.variables[var_name].dimensions)
            # get variable specific dimensions
            spec_dimensions = list(filter(self._filter_dimensions, var_dimensions))
            # Replace <time> dimension by index of requested time slice
            var_dimensions[var_dimensions.index(self.timeVarName)] = layer_time_id
            var_dimensions[var_dimensions.index(self.yName)] = 'y'
            var_dimensions[var_dimensions.index(self.xName)] = 'x'

            if spec_dimensions:
                # Handle only one (first in the list) additional dimension
                for i in range(self.ds.dimensions[spec_dimensions[0]].size):
                    var_dimensions_copy = var_dimensions.copy()
                    var_dimensions_copy[var_dimensions_copy.index(spec_dimensions[0])] = i
                    meta_dict.append(self.get_metaitem(filename, var_name, var_dimensions_copy))
            else:
                meta_dict.append(self.get_metaitem(filename, var_name, var_dimensions))
        print(meta_dict)
        self.create_bands(meta_dict)

        # set time
        time_res_sec = self.get_time_coverage_resolution()
        self.dataset.SetMetadataItem('time_coverage_start', str(layer_date))
        self.dataset.SetMetadataItem('time_coverage_end', str(layer_date + time_res_sec))

    def _filter_dimensions(self, dim_name):
        """Check if an input name is in a list of standard names"""
        if dim_name not in [self.timeVarName, self.yName, self.xName]:
            return True

    def get_time_coverage_resolution(self):
        """Try to fecth time_coverage_resolution and convert to seconds"""
        time_res_sec = 0
        if 'time_coverage_resolution' in self.ds.ncattrs():
            time_res = self.ds.time_coverage_resolution
            try:
                time_res_sec = int(time_res[1]) * self.P2S[time_res[2].upper()]
            except:
                warnings.warn('Cannot get time_coverage_resolution')

        return time_res_sec

    def get_shape(self):
        """Get srcRasterXSize and srcRasterYSize from OpenDAP"""
        return self.ds.variables[self.xName].size, self.ds.variables[self.yName].size

    def get_geotransform(self):
        """Get first two values of X,Y variables and create geoTranform"""

        xx = self.ds.variables[self.xName][0:2]
        yy = self.ds.variables[self.yName][0:2]
        return xx[0], xx[1]-xx[0], 0, yy[0], 0, yy[1]-yy[0]

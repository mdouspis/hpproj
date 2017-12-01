#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2016 IAS / CNRS / Univ. Paris-Sud
# LGPL License - see attached LICENSE file
# Author: Alexandre Beelen <alexandre.beelen@ias.u-psud.fr>

import pytest

from hpproj import hp_celestial, hp_is_nest
from hpproj import hp_to_wcs, hp_to_wcs_ipx
from hpproj import hp_project, gen_hpmap, hpmap_key

from hpproj import build_wcs

import numpy as np
from numpy import testing as npt
import healpy as hp
from astropy.coordinates import ICRS, Galactic, SkyCoord
from astropy.io import fits


class TestHPCelestical:

    @pytest.mark.parametrize("hp_header", [{},
                                           {'COORDSYS': 'ecliptic'},
                                           {'COORDSYS': 'ECLIPTIC'},
                                           {'COORDSYS': 'e'},
                                           {'COORDSYS': 'E'}, ])
    def test_hp_celestial_exception(self, hp_header):
        with pytest.raises(ValueError):
            frame = hp_celestial(hp_header)

    @pytest.mark.parametrize("hp_header,result",
                             [({'COORDSYS': 'G'}, Galactic()),
                              ({'COORDSYS': 'Galactic'}, Galactic()),
                              ({'COORDSYS': 'Equatorial'}, ICRS()),
                              ({'COORDSYS': 'EQ'}, ICRS()),
                              ({'COORDSYS': 'celestial2000'}, ICRS()),
                              ])
    def test_hp_celestial(self, hp_header, result):
        frame = hp_celestial(hp_header)
        assert frame.is_equivalent_frame(result)


class TestHPNest:

    def test_hp_is_nest_exception(self):
        hp_headers = [{}, {'ORDERING': 'Unknown'}]

        for hp_header in hp_headers:
            with pytest.raises(ValueError):
                is_nest = hp_is_nest(hp_header)

    @pytest.mark.parametrize("hp_header, result",
                             [({'ORDERING': 'nested'}, True),
                              ({'ORDERING': 'NESTED'}, True),
                              ({'ORDERING': 'ring'}, False),
                              ])
    def test_hp_is_nest(self, hp_header, result):
        is_nest = hp_is_nest(hp_header)
        assert is_nest == result


def test_hp_to_wcs_exception():

    nside = 2**6
    hp_map = np.ones(hp.nside2npix(nside))
    hp_header = {'NSIDE': nside,
                 'ORDERING': 'RING',
                 'COORDSYS': 'C'}
    hp_hdu = fits.ImageHDU(hp_map, fits.Header(hp_header))

    coord, pixsize, shape_out = SkyCoord(0, 0, unit='deg'), 1, [512, 512]
    wcs = build_wcs(coord, pixsize, shape_out)

    # Test order > 1
    with pytest.raises(ValueError):
        sub_map = hp_to_wcs(hp_hdu, wcs, shape_out=shape_out, order=2)


def test_hp_to_wcs():
    # hp_to_wcs(hp_map, hp_header, wcs, shape_out=DEFAULT_shape_out,
    # npix=None, order=0):

    nside = 2**6
    hp_map = np.ones(hp.nside2npix(nside))
    hp_header = {'NSIDE': nside,
                 'ORDERING': 'RING',
                 'COORDSYS': 'C'}
    hp_hdu = fits.ImageHDU(hp_map, fits.Header(hp_header))

    coord, pixsize, shape_out = SkyCoord(
        0, 0, unit='deg'), np.degrees(hp.nside2resol(nside)), [512, 512]
    wcs = build_wcs(coord, pixsize, shape_out)

    # Test order = 0
    sub_map = hp_to_wcs(hp_hdu, wcs, shape_out=shape_out, order=0)
    assert sub_map.shape == tuple(shape_out)
    npt.assert_array_equal(sub_map, 1)

    # Test order = 1
    sub_map = hp_to_wcs(hp_hdu, wcs, shape_out=shape_out, order=1)
    npt.assert_allclose(sub_map, 1, rtol=1e-15)  # hp.get_interp_val precision

    # Test specific pixel Better use an odd number for this, because
    # build_wcs put the reference at the center of the image, which in
    # case of even number leaves it between 4 pixels and hp.ang2pix
    shape_out = [3, 3]

    wcs = build_wcs(coord, pixsize, shape_out)

    lon, lat = coord.ra.deg, coord.dec.deg
    phi, theta = np.radians(lon), np.radians(90 - lat)
    ipix = hp.ang2pix(nside, theta, phi, nest=hp_is_nest(hp_header))
    hp_map[ipix] = 0
    sub_map = hp_to_wcs(hp_hdu, wcs, shape_out=shape_out, order=0)
    i_x, i_y = wcs.all_world2pix(lon, lat, 0)
    assert sub_map[int(np.floor(i_y + 0.5)), int(np.floor(i_x + 0.5))] == 0

    # Test different frame
    wcs = build_wcs(coord, pixsize, shape_out, proj_sys="G")
    sub_map = hp_to_wcs(hp_hdu, wcs, shape_out=shape_out)
    lon, lat = coord.galactic.l.deg, coord.galactic.b.deg
    i_x, i_y = wcs.all_world2pix(lon, lat, 0)
    assert sub_map[int(np.floor(i_y + 0.5)), int(np.floor(i_x + 0.5))] == 0


def test_hp_to_wcs_ipx():

    nside = 2**6
    hp_header = {'NSIDE': nside,
                 'ORDERING': 'RING',
                 'COORDSYS': 'C'}

    coord, pixsize, shape_out = SkyCoord(0, 0, unit='deg'), 0.1, [1, 1]
    wcs = build_wcs(coord, pixsize, shape_out)

    # Basic test
    sub_mask, sub_ipx = hp_to_wcs_ipx(hp_header, wcs, shape_out=shape_out)
    lon, lat = coord.ra.deg, coord.dec.deg
    phi, theta = np.radians(lon), np.radians(90 - lat)
    ipix = hp.ang2pix(nside, theta, phi, nest=hp_is_nest(hp_header))

    npt.assert_array_equal(sub_mask, True)
    npt.assert_array_equal(sub_ipx, ipix)

    # Test different frame
    wcs = build_wcs(coord, pixsize, shape_out=(1, 1), proj_sys="G")
    sub_mask, sub_ipx = hp_to_wcs_ipx(
        hp_header, wcs, shape_out=shape_out)
    npt.assert_array_equal(sub_mask, True)
    npt.assert_array_equal(sub_ipx, ipix)


def test_hp_project():
    nside = 2**6
    hp_map = np.ones(hp.nside2npix(nside))
    hp_header = {'NSIDE': nside,
                 'ORDERING': 'RING',
                 'COORDSYS': 'C'}
    hp_hdu = fits.ImageHDU(hp_map, fits.Header(hp_header))

    coord, pixsize, npix = SkyCoord(
        0, 0, unit='deg'), np.degrees(hp.nside2resol(nside)), 512

    # Test HDU
    sub_map = hp_project(hp_hdu, coord, pixsize, npix)
    assert type(sub_map) is fits.hdu.image.PrimaryHDU
    assert sub_map.data.shape == (npix, npix)


def test_hpmap_decorator():
    nside = 2**6
    hp_map = np.ones(hp.nside2npix(nside))
    hp_header = {'NSIDE': nside,
                 'ORDERING': 'RING',
                 'COORDSYS': 'C'}

    coord, pixsize, npix = SkyCoord(
        0, 0, unit='deg'), np.degrees(hp.nside2resol(nside)), 512

    # Test HDU
    sub_map = hp_project(hp_map, hp_header, coord, pixsize, npix)
    assert type(sub_map) is fits.hdu.image.PrimaryHDU
    assert sub_map.data.shape == (npix, npix)


def test_gen_hpmap():

    nside = 2**6
    hp_map = np.ones(hp.nside2npix(nside))
    hp_header = {'NSIDE': nside,
                 'ORDERING': 'RING',
                 'COORDSYS': 'C'}

    maps = [('map' + str(i), hp_map * i, hp_header) for i in range(3)]

    for i, (name, hp_hdu) in enumerate(gen_hpmap(maps)):
        assert name == 'map' + str(i)
        npt.assert_array_equal(hp_hdu.data, i)


def test_hpmap_key():

    hp_map = ('dummy', 'dummy', {'NSIDE': 32,
                                 'ORDERING': 'RING',
                                 'COORDSYS': 'C'})
    key = hpmap_key(hp_map)

    assert isinstance(key, str)
    assert key == u'32_RING_icrs'

# def test_group_hpmap():

#     nside = 2**6

#     hp_headers = [{'NSIDE': nside,
#                     'ORDERING': 'RING',
#                     'COORDSYS': 'C'},
#                    {'NSIDE': nside,
#                     'ORDERING': 'NEST',
#                     'COORDSYS': 'C'},
#                    {'NSIDE': nside/2,
#                     'ORDERING': 'RING',
#                     'COORDSYS': 'C'},
#                    {'NSIDE': nside,
#                     'ORDERING': 'RING',
#                     'COORDSYS': 'G'} ]

# hp_keys = ["%s_%s_%s"%(hp_header['NSIDE'], hp_header['ORDERING'],
# hp_celestial(hp_header).name) for hp_header in hp_headers]

#     maps = [('dummy_'+str(i),'dummy_'+str(i),hp_header) for i,hp_header in enumerate(hp_headers) ]
#     maps.append(('dummy_4', 'dummy_4', hp_headers[0]))

#     grouped_maps = group_hpmap(maps)

# First and last should be grouped
#     assert grouped_maps[hp_keys[0]] == [maps[0], maps[-1]]

# Test the singletons (all but first and last)
#     for i,key in enumerate(hp_keys[1:]):
#         assert len(grouped_maps[key]) == 1
#         assert grouped_maps[key][0] == maps[i+1]

#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2016 IAS / CNRS / Univ. Paris-Sud
# LGPL License - see attached LICENSE file
# Author: Alexandre Beelen <alexandre.beelen@ias.u-psud.fr>

"""Series of helper function to deal with healpix maps"""

from __future__ import print_function, division

from itertools import repeat

import logging
import numpy as np
import healpy as hp

from astropy.io import fits
from astropy.wcs import WCS, utils as wcs_utils
from astropy.coordinates import SkyCoord, UnitSphericalRepresentation
from astropy import units as u

from .wcs_helper import equiv_celestial, build_wcs, get_lonlat
from .wcs_helper import build_wcs_profile, build_wcs_cube
from .wcs_helper import DEFAULT_SHAPE_OUT

from .decorator import _hpmap

logging.basicConfig(format='%(asctime)s -- %(levelname)s: %(message)s', level=logging.DEBUG)

__all__ = ['hp_is_nest', 'hp_celestial', 'hp_to_wcs', 'hp_to_wcs_ipx',
           'hp_project', 'gen_hpmap', 'build_hpmap', 'hpmap_key',
           'wcs_to_profile', 'hp_to_profile', 'hp_profile', 'hp_stack']


def hp_celestial(hp_header):
    """Retrieve the celestial system used in healpix maps. From Healpix documentation this can have 3 forms :

    - 'EQ', 'C' or 'Q' : Celestial2000 = eQuatorial,
    - 'G' : Galactic
    - 'E' : Ecliptic,

    only Celestial and Galactic are supported right now as the Ecliptic coordinate system was just recently pulled to astropy

    Similar to :class:`~astropty.wcs.utils.wcs_to_celestial_frame` but for header from healpix maps

    Parameters
    ----------
    hp_header : :class:`~astropy.io.fits.header.Header`
        the header of the healpix map

    Returns
    -------
    frame : :class:`~astropy.coordinates.baseframe.BaseCoordinateFrame` subclass instance
        An instance of a :class:`~astropy.coordinates.baseframe.BaseCoordinateFrame`
        subclass instance that best matches the specified WCS.

    """
    coordsys = hp_header.get('COORDSYS')

    if coordsys:
        return equiv_celestial(coordsys)
    else:
        raise ValueError("No COORDSYS in header")


def hp_is_nest(hp_header):
    """Return True if the healpix header is in nested

    Parameters
    ----------
    hp_header : :class:`~astropy.io.fits.header.Header`
        the header 100
    -------
    bool :
        True if the header is nested
    """

    ordering = hp_header.get('ORDERING', None)

    if not ordering:
        raise ValueError("Np ordering in healpix header")

    if ordering.lower() in ['nested', 'nest']:
        return True
    elif ordering.lower() == 'ring':
        return False
    else:
        raise ValueError("Unknown ordering in healpix header")


def rotate_frame(alon, alat, hp_header, wcs):
    """Change frame if needed

    Parameters
    ----------
    alon, alat : array_like
        longitudes and latitudes describe by the
    hp_header : :class:`astropy.fits.header.Header`
        corresponding header
    wcs : :class:`~astropy.wcs.WCS`
        An corresponding wcs object

    Returns
    -------
    tuple of array_like
        longitude and latitude arrays rotated if neeed
    """

    # Determine if we need a rotation for different coordinate systems
    frame_in = hp_celestial(hp_header)
    frame_out = wcs_utils.wcs_to_celestial_frame(wcs)

    if not frame_in.is_equivalent_frame(frame_out):
        logging.debug('... converting coordinate system')
        coords = frame_out.realize_frame(
            UnitSphericalRepresentation(alon * u.deg, alat * u.deg)).transform_to(frame_in)
        alon = coords.data.lon.deg
        alat = coords.data.lat.deg

    return alon, alat


def wcs_to_profile(hdu, wcs, shape_out=DEFAULT_SHAPE_OUT[0]):
    """Centered profile from 2D map

    Parameters
    ----------
    hdu : :class:`astropy.fits.ImageHDU`
        hdu containing the 2D array and corresponding header, the profile will be made from the CRVAL position
    wcs : :class:`astropy.wcs.WCS`
        wcs object to describe the radius of the profile
    shape_out : int
        shape of the output profile

    Returns
    -------
    :class:`astropy.fits.ImageHDU`
        1D hdu image containing the profile and the corresponding header
    """

    frame = wcs_utils.wcs_to_celestial_frame(WCS(hdu.header)).name
    coord = SkyCoord(hdu.header['CRVAL1'], hdu.header['CRVAL2'], unit="deg", frame=frame)

    shape = hdu.shape
    yy, xx = np.indices(shape)
    lon_arr, lat_arr = WCS(hdu.header).wcs_pix2world(xx, yy, 0)

    coords = SkyCoord(lon_arr, lat_arr, unit="deg", frame=frame)
    dist = coords.separation(coord).to(u.deg).value

    r_edge = wcs.all_pix2world(np.arange(shape_out + 1) - 0.5, 0)[0]

    hist, bin_edges = np.histogram(dist, bins=r_edge, weights=hdu.data)
    hist_d, bin_edges_d = np.histogram(dist, bins=r_edge)

    return hist / hist_d


@_hpmap
def hp_to_profile(hp_hdu, wcs, coord, shape_out=DEFAULT_SHAPE_OUT[0], std=False):
    """Extract radial profile from healpix map

    Parameters
    ----------
    hp_hdu : `:class:astropy.io.fits.ImageHDU`
        a pseudo ImageHDU with the healpix map and the associated header
    wcs : :class:`astropy.wcs.WCS`
        wcs object to describe the radius of the profile
    coord : :class:`astropy.coordinate.SkyCoord`
        the sky coordinate of the center of the profile
    shape_out : int
        shape of the output profile
    std : bool
        return the standard deviation

    Returns
    -------
    :class:`astropy.fits.ImageHDU`
        1D hdu image containing the profile and the corresponding header,
        optionnaly a second ImageHDU containing the standard deviation
    """

    lon, lat = get_lonlat(coord, hp_hdu.header['COORDSYS'])

    r_edge = wcs.all_pix2world(np.arange(shape_out + 1) - 0.5, 0)[0]

    nside = hp_hdu.header['NSIDE']
    nest = hp_is_nest(hp_hdu.header)
    vec = hp.rotator.dir2vec(lon, lat, lonlat=True)

    i_pix = [hp.query_disc(nside, vec, np.radians(radius), nest=nest, inclusive=False) for radius in r_edge]

    # Only keep the pixels why are only present in the outer
    # radius
    a_pix = []
    for in_pix, out_pix in zip(i_pix[:-1], i_pix[1:]):
        a_pix.append(np.setxor1d(in_pix, out_pix))

    profile = np.asarray([np.mean(hp_hdu.data[pix]) for pix in a_pix])
    if not std:
        return profile
    else:
        std_profile = np.asarray([np.std(hp_hdu.data[pix]) for pix in a_pix])
        return profile, std_profile


@_hpmap
def hp_to_wcs(hp_hdu, wcs, shape_out=DEFAULT_SHAPE_OUT, order=0):
    """Project an Healpix map on a wcs header, using nearest neighbors.

    Parameters
    ----------
    hp_hdu : `:class:astropy.io.fits.ImageHDU`
        a pseudo ImageHDU with the healpix map and the associated header
    wcs : :class:`astropy.wcs.WCS`
        wcs object to project with
    shape_out : tuple
        shape of the output map (n_y, n_x)
    order : int (0|1)
        order of the interpolation 0: nearest-neighbor, 1: bi-linear interpolation

    Returns
    -------
    array_like
        the projected map in a 2D array of shape shape_out
    """

    # Array of pixels centers -- from the astropy.wcs documentation :
    #
    # Here, *origin* is the coordinate in the upper left corner of the
    # image.In FITS and Fortran standards, this is 1.  In Numpy and C
    # standards this is 0.
    #
    # This as we are using the C convention, the center of the first pixel is 0, 0
    y_tab, x_tab = np.indices(shape_out)

    alon, alat = wcs.wcs_pix2world(x_tab, y_tab, 0)
    # mask for pixels lying outside of projected area
    mask = ~np.logical_or(np.isnan(alon), np.isnan(alat))
    proj_map = np.ma.array(np.zeros(shape_out), mask=~mask, fill_value=np.nan)

    # Determine if we need a rotation for different coordinate systems
    alon, alat = rotate_frame(alon, alat, hp_hdu.header, wcs)

    # Healpix conventions...
    phi = np.radians(alon)
    theta = np.radians(90 - alat)

    if order == 0:
        ipix = hp.ang2pix(hp.npix2nside(len(hp_hdu.data)),
                          theta[mask], phi[mask],
                          nest=hp_is_nest(hp_hdu.header))
        proj_map[mask] = hp_hdu.data[ipix]
    elif order == 1:
        proj_map[mask] = hp.get_interp_val(
            hp_hdu.data, theta[mask], phi[mask], nest=hp_is_nest(hp_hdu.header))
    else:
        raise ValueError("Unsupported order for the interpolation")

    return proj_map.filled()


def hp_to_wcs_ipx(hp_header, wcs, shape_out=DEFAULT_SHAPE_OUT):
    """Return the indexes of pixels of a given wcs and shape_out,
    within a nside healpix map.

    Parameters
    ----------
    hp_header : :class:`astropy.fits.header.Header`
        header of the healpix map, should contain nside and coordsys and ordering
    wcs : :class:`astropy.wcs.WCS`
        wcs object to project with
    shape_out : tuple
        shape of the output map (n_y, n_x)

    Returns
    -------
    2D array_like
        mask for the given map
    array_like
        corresponding pixel indexes

    Notes
    -----
    The map could then easily be constructed using

    proj_map = np.ma.array(np.zeros(shape_out), mask=~mask, fill_value=np.nan)
    proj_map[mask] = healpix_map[ipix]
    """

    # Array of pixels centers -- from the astropy.wcs documentation :
    #
    # Here, *origin* is the coordinate in the upper left corner of the
    # image.In FITS and Fortran standards, this is 1.  In Numpy and C
    # standards this is 0.
    #
    # This as we are using the C convention, the center of the first pixel is 0, 0
    y_tab, x_tab = np.indices(shape_out)

    alon, alat = wcs.wcs_pix2world(x_tab, y_tab, 0)
    # mask for pixels lying outside of projected area
    mask = ~np.logical_or(np.isnan(alon), np.isnan(alat))

    nside = hp_header['NSIDE']

    # Determine if we need a rotation for different coordinate systems
    alon, alat = rotate_frame(alon, alat, hp_header, wcs)

    # Healpix conventions...
    phi = np.radians(alon)
    theta = np.radians(90 - alat)

    ipix = hp.ang2pix(nside, theta[mask], phi[mask], nest=hp_is_nest(hp_header))

    return mask, ipix


@_hpmap
def hp_project(hp_hdu, coord, pixsize=0.01, shape_out=DEFAULT_SHAPE_OUT, order=0, projection=('GALACTIC', 'TAN')):
    """Project an healpix map at a single given position

    Parameters
    ----------
    hp_hdu : `:class:astropy.io.fits.ImageHDU`
        a pseudo ImageHDU with the healpix map and the associated header to be projected
    coord : :class:`astropy.coordinate.SkyCoord`
        the sky coordinate of the center of the projection
    pixsize : float
        size of the pixel (in degree)
    shape_out : tuple
        shape of the output map (n_y, n_x)
    order : int (0|1)
        order of the interpolation 0: nearest-neighbor, 1: bi-linear interpolation
    projection : tuple of str
        the coordinate ('GALACTIC', 'EQUATORIAL') and projection ('TAN', 'SIN', 'GSL', ...) system
    hdu : bool
        return a :class:`astropy.io.fits.PrimaryHDU` instead of just a ndarray

    Returns
    -------
    :class:`astropy.io.fits.PrimaryHDU`
        containing the array and the corresponding header
    """

    proj_sys, proj_type = projection

    wcs = build_wcs(coord, pixsize, shape_out=shape_out, proj_sys=proj_sys, proj_type=proj_type)
    proj_map = hp_to_wcs(hp_hdu, wcs, shape_out=shape_out, order=order)

    return fits.ImageHDU(proj_map, wcs.to_header(relax=0x20000))


@_hpmap
def hp_stack(hp_hdu, coords, pixsize=0.01, shape_out=DEFAULT_SHAPE_OUT, order=0, projection=('GALACTIC', 'TAN'), keep=False):

    proj_sys, proj_type = projection

    lons, lats = get_lonlat(coords, proj_sys)

    fake_ref = SkyCoord(0, 0, frame=coords[0].frame.name, unit="deg")

    if isinstance(pixsize, (int, float)):
        ref_pixsize = pixsize
        pixsize = repeat(pixsize, len(coords))
    else:
        ref_pixsize = pixsize[0]

    w = build_wcs_cube(fake_ref, 0, pixsize=ref_pixsize, shape_out=shape_out,
                       proj_sys=proj_sys, proj_type=proj_type)

    _w = w.dropaxis(2)

    stacks = np.ma.zeros(shape_out)
    if keep:
        # return 3D array
        stacks = np.ma.resize(stacks, (len(coords),) + stacks.shape)

    for index, (lon, lat, pix) in enumerate(zip(lons, lats, pixsize)):

        _w.wcs.crval[0] = lon
        _w.wcs.crval[1] = lat
        _w.wcs.cdelt = [-pix, pix]

        if keep:
            stacks[index, :, :] = hp_to_wcs(hp_hdu, _w, shape_out)
        else:
            stacks += hp_to_wcs(hp_hdu, _w, shape_out)

    # Take the mean
    if not keep:
        stacks /= len(coords)
        w.dropaxis(2)

    return fits.ImageHDU(stacks, w.to_header(relax=0x20000))


@_hpmap
def hp_profile(hp_hdu, coord, pixsize=0.01, npix=512):
    """Project an healpix map at a single given position

    Parameters
    ----------
    hp_hdu : `:class:astropy.io.fits.ImageHDU`
        a pseudo ImageHDU with the healpix map and the associated header to be projected
    coord : :class:`astropy.coordinate.SkyCoord`
        the sky coordinate of the center of the projection
    pixsize : float
        size of the pixel (in degree)
    npix : int
        number of pixels in the final map, the reference pixel will be at the center

    Returns
    -------
    :class:`astropy.io.fits.PrimaryHDU`
        containing the array and the corresponding header
    """

    wcs = build_wcs_profile(pixsize)
    profile = hp_to_profile(hp_hdu, wcs, coord, shape_out=npix, std=False)

    return fits.ImageHDU(profile, wcs.to_header(relax=0x20000))


def gen_hpmap(maps):
    """Generator function for large maps and low memory system

    Parameters
    ----------
    maps : list
        A list of Nmap tuples with either:
            * (filename, path_to_localfilename, healpix header)
            * (filename, healpix vector, healpix header)

    Returns
    -------
    tuple
        Return a tuple (filename, healpix map, healpix header) corresponding to the inputed list
    """
    for filename, i_map, i_header in maps:
        if isinstance(i_map, str):
            i_map = hp.read_map(i_map, verbose=False, nest=None)
            i_map = hp.ma(i_map)
        yield filename, fits.ImageHDU(i_map, fits.Header(i_header))


def build_hpmap(filenames, low_mem=True):
    """From a filename list, build a tuple usable with gen_hmap()

    Parameters
    ----------
    filenames: list
        A list of Nmap filenames of healpix maps
    low_mem : bool
        On low memory system, do not read the maps themselves (default: only header)

    Returns
    -------
    tuple list
        A list of tuple which can be used by gen_hpmap    """

    hp_maps = []
    for filename in filenames:
        hp_header = fits.getheader(filename, 1)
        if low_mem is True:
            hp_map = filename
        else:
            hp_map = hp.read_map(filename, verbose=False, nest=None)
            hp_map = hp.ma(hp_map)
        hp_maps.append((filename, hp_map, hp_header))
    return hp_maps


def hpmap_key(hp_map):
    """Generate an key from the hp_map tuple to sort the hp_maps by map
    properties

    Parameters
    ----------
    hp_map: tuple
        A tuple from (build|gen)_hpmap : (filename, healpix map, healpix header)

    Returns
    -------
    str
        A string with the map properties
    """
    i_header = hp_map[2]

    return "%s_%s_%s" % (i_header['NSIDE'], i_header['ORDERING'], hp_celestial(i_header).name)

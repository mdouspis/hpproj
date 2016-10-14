#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2016 IAS / CNRS / Univ. Paris-Sud
# LGPL License - see attached LICENSE file
# Author: Alexandre Beelen <alexandre.beelen@ias.u-psud.fr>

import logging
logger = logging.getLogger('django')

import warnings
import os, sys
import argparse

import numpy as np
import healpy as hp

# try: # prama: no cover
#     from wcsaxes import WCS # (deprecated)
# except ImportError: # pragma: no cover
from astropy.wcs import WCS

from astropy.io import fits
from astropy import units as u
from astropy.coordinates import SkyCoord
from photutils import CircularAperture
from photutils import aperture_photometry

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try: # pragma: py3
    from configparser import ConfigParser, ExtendedInterpolation
    pattern = None
except ImportError: # pragma: py2
    import re
    from ConfigParser import ConfigParser
    pattern = re.compile(r"\$\{(.*?)\}")

try: # pragma: py3
    from io import BytesIO
except ImportError: # pragma: py2
    from cStringIO import StringIO as BytesIO

from base64 import b64encode

try: # pragma: py3
    FileNotFoundError
except NameError: # pragma: py2
    FileNotFoundError = IOError

from .hp_helper import build_WCS, hp_to_wcs_ipx
from .hp_helper import VALID_PROJ, VALID_EQUATORIAL, VALID_GALACTIC
from .hp_helper import equiv_celestial
from .hp_helper import build_hpmap, group_hpmap, gen_hpmap

DEFAULT_npix = 256
DEFAULT_pixsize = 1
DEFAULT_coordframe = 'galactic'
DEFAULT_ctype = 'TAN'

class CutSky(object):
    """
    Container for Healpix maps and cut_* methods

    ...

    Attributes
    ----------
    npix : int
        the number of pixels for the square maps
    pixsize : float
        the size of the pixels [arcmin]
    ctype : str
        a valid projection type (default : TAN)
    maps : dictonnary
        a grouped dictionnary of gen_hpmap tuples (filename, map, header)


    """

    def __init__(self, maps=None, npix=DEFAULT_npix, pixsize=DEFAULT_pixsize, ctype=DEFAULT_ctype, low_mem=True):
        """Initialization of a CutSky class

        Parameters
        ----------
        maps : list of tuple or dictionary
            list of tuple (filename, {opt}) where filename is the full
            path to the healpix map and {opt} is a dictionnary with
            "optional" header for the file (see Notes).
        npix : int
            the number of pixels for the square maps
        pixsize : float
            the size of the pixels [arcmin]
        ctype : str
            a valid projection type (default : TAN)
        low_mem: bool
            a boolean deciding if all the maps are loaded in memory or
            not (default: True, the maps are not loaded on init)

        Notes
        -----
        The list of dictionnary describe the maps to be projected :
        ```
        [(full_filename_to_healpix_map.fits, {'legend': legend,
                                              'doContour': True}), # optionnal
          ... ]
        ```
        The {opt} dictionnary MUST containt, at least, the key
        'legend' which will be used to uniquely identify the cutted
        map, other possible keys 'doContour' with a boolean value to
        contour the map latter one

        Alternatively one can use the old interface as a dictionnary :
        ```
        {legend: {'filename': full_filename_to_healpix_map.fits,
                    'doContour': True }, # optionnal
         ... }
        ```
        """

        if maps is None:
            raise FileNotFoundError("No healpix map to project")

        if isinstance(maps, dict):
            maps = to_new_maps(maps)

        # Define the basic parameters for the output maps
        self.npix = npix
        self.pixsize = pixsize
        self.ctype = ctype

        # Build an hp_map list and extend the headers of the maps with
        # the optionnal values
        filenames, opts = [filename for filename, opt in maps], [opt for filename, opt in maps]
        hp_map = build_hpmap(filenames, low_mem=low_mem)
        for (filename, iMap, iHeader), opt in zip(hp_map, opts):
            iHeader.extend(opt)

        # group them by map properties for efficiencies reasons
        self.maps = group_hpmap(hp_map)

        # Save intermediate results
        self.maps_selection = None
        self.cuts = None
        self.lonlat = None
        self.coordframe = DEFAULT_coordframe

    def cut_fits(self,lonlat = [0, 0], coordframe = DEFAULT_coordframe, maps_selection=None):
        """Efficiently cut the healpix maps and return cutted fits file with proper header

        Parameters
        ----------
        lonlat : array of 2 floats
            the longitude and latitude of the center of projection [deg]
        coordframe : str
            the coordinate frame used for the position AND the projection
        maps_selection : list
            optionnal list of the 'legend' of the map to select a
            sub-sample of them.

        Returns
        -------
        list of dictionnaries
            the dictionnary has 2 keys :
            * 'legend' (the opts{'legend'} see __init())
            * 'fits' an ~astropy.io.fits.ImageHDU

        """

        self.maps_selection = maps_selection

        # Center of projection
        coord_in = SkyCoord(lonlat[0],lonlat[1], unit=u.deg, frame=equiv_celestial(coordframe))

        # Build the target WCS header
        w = build_WCS(coord_in, pixsize=self.pixsize/60., npix=self.npix, proj_sys=coordframe, proj_type=self.ctype)

        cuts = []
        for group in self.maps.keys():
            logger.info('projecting '+group)

            maps = self.maps[group]

            # Construct a basic healpix header from the group key, this
            # will be commun for all the maps in this group
            hp_header = maps[0][2]

            # Extract mask & pixels, common for all healpix maps of this
            # group
            mask, ipix = hp_to_wcs_ipx(hp_header, w, npix=self.npix)

            # Set up the figure, common for all healpix maps of this group
            logger.info('cutting maps')

            # Now the actual healpix map reading and projection
            for filename, iMap, iHeader in gen_hpmap(maps):
                legend = iHeader['legend']

                # Skip if not in the maps_selection
                if self.maps_selection and legend not in self.maps_selection:
                    continue

                patch = np.ma.array(np.zeros((self.npix, self.npix)), mask=~mask, fill_value=np.nan)
                patch[mask] = iMap[ipix]
                header = w.to_header()
                header.append(('filename',filename) )
                header.append(('legend', legend) )
                if 'doContour' in iHeader.keys():
                    header.append(('doContour', iHeader['doContour']))

                cuts.append( {'legend': legend,
                              'fits': fits.ImageHDU(patch, header)} )
        self.lonlat = lonlat
        self.coordframe = coordframe
        self.cuts = cuts

        return cuts

    def cut_png(self, lonlat = [0, 0], coordframe = DEFAULT_coordframe, maps_selection=None):
        """Efficiently cut the healpix maps and return cutted fits file with proper header and corresponding png

        Parameters
        ----------
        lonlat : array of 2 floats
            the longitude and latitude of the center of projection [deg]
        coordframe : str
            the coordinate frame used for the position AND the projection
        maps_selection : list
            optionnal list of the 'legend' of the map to select a
            sub-sample of them.

        Returns
        -------
        list of dictionnaries
            the dictionnary has 3 keys :
            * 'legend' (the opts{'legend'} see __init()),
            * 'fits' an ~astropy.io.fits.ImageHDU,
            * 'png', a b61encoded png image of the fits
        """

        if self.lonlat == lonlat and \
           self.coordframe == coordframe and  \
           self.maps_selection == maps_selection and \
           self.cuts:
            # Retrieve previously cut maps
            cuts = self.cuts
        else:
            # Or cut the maps
            cuts = self.cut_fits(lonlat=lonlat, coordframe=coordframe)

        # Plotting
        patch = np.zeros((self.npix, self.npix))
        fig=plt.figure()

        wcs_proj=WCS(cuts[0]['fits'])
        ax_wcs=fig.add_axes([0.1,0.1,0.9,0.9],projection=wcs_proj)
        proj_im = ax_wcs.imshow(patch, interpolation='none', origin='lower' )
        ax_wcs.coords.grid(color='green', linestyle='solid', alpha=0.5)

        if np.str(coordframe) in VALID_EQUATORIAL:
            ax_wcs.coords['ra'].set_ticks(color='white')
            ax_wcs.coords['dec'].set_ticks(color='white')
            ax_wcs.coords['ra'].set_axislabel(r'$\alpha_\mathrm{J2000}$')
            ax_wcs.coords['dec'].set_axislabel(r'$\delta_\mathrm{J2000}$')
        elif np.str(coordframe) in VALID_GALACTIC:
            ax_wcs.coords['glon'].set_ticks(color='red')
            ax_wcs.coords['glat'].set_ticks(color='red')
            ax_wcs.coords['glon'].set_axislabel(r'$l$')
            ax_wcs.coords['glat'].set_axislabel(r'$b$')

        for cut in cuts :

            legend = cut['legend']
            logger.debug('plotting '+ legend)

            patch = cut['fits'].data
            patch_header = cut['fits'].header

            proj_im.set_data(patch)
            proj_im.set_clim(vmin=patch.min(), vmax=patch.max())

            if 'doContour' in patch_header.keys() and patch_header['doContour']:
                logger.debug('contouring '+legend)
                levels=[patch.max()/3., patch.max()/2.]
                if ((patch.max()-patch.mean()) > 3*patch.std()):
                    proj_cont = ax_wcs.contour(patch,levels=levels,colors="white",interpolation='bicubic')
                else:
                    proj_cont = None

            logger.debug('saving '+legend)

            # Add the map to the cut dictionnary
            output_map = BytesIO()
            plt.savefig(output_map,bbox_inches='tight', format='png',dpi=75, frameon=False)
            cut['png'] = b64encode(output_map.getvalue()).strip()
            logger.debug(legend+ ' done')

        return cuts

    def cut_phot(self, lonlat = [0, 0], coordframe = DEFAULT_coordframe, maps_selection=None):
        """Efficiently cut the healpix maps and return cutted fits file with proper header and corresponding photometry

        Parameters
        ----------
        lonlat : array of 2 floats
            the longitude and latitude of the center of projection [deg]
        coordframe : str
            the coordinate frame used for the position AND the projection
        maps_selection : list
            optionnal list of the 'legend' of the map to select a
            sub-sample of them.

        Returns
        -------
        list of dictionnaries
            the dictionnary has 3 keys :
            * 'legend' (the opts{'legend'} see __init()),
            * 'fits' an ~astropy.io.fits.ImageHDU,
            * 'phot', the corresponding photometry
        """

        if self.lonlat == lonlat and \
           self.coordframe == coordframe and  \
           self.maps_selection == maps_selection and \
           self.cuts:
            # Retrieve previously cut maps
            cuts = self.cuts
        else:
            # Or cut the maps
            cuts = self.cut_fits(lonlat=lonlat, coordframe=coordframe)

        positions = [(self.npix*1./2., self.npix*1./2) ]
        apertures = CircularAperture(positions, r = 3./self.pixsize)

        for cut in cuts :
            legend = cut['legend']
            logger.debug('phot on '+ legend)

            patch = cut['fits'].data
            cut['phot'] = aperture_photometry(patch-np.median(patch), apertures)

            logger.debug(legend+ ' done')

        return cuts

def to_new_maps(maps):
    """Transform old dictionnary type healpix map list used by cutsky to
    list of tuple used by Cutsky

    Parameters
    ----------
    maps : dict
        a dictionnary with key being the legend of the image :
        ```
        {legend: {'filename': full_filename_to_healpix_map.fits,
                    'doContour': True }, # optionnal
         ... }
        ```

    Returns
    -------
    a list of tuple following the new convention:
    ```
    [(full_filename_to_healpix_map.fits, {'legend': legend,
                                          'doContour': True}), # optionnal
     ... ]
    ```
    """

    warnings.warn("deprecated", DeprecationWarning)

    new_maps = []
    for key in iter(maps.keys()):
        filename = maps[key]['filename']
        opt = {'legend': key}
        if 'doContour' in maps[key]:
            opt['doContour'] = maps[key]['doContour']
        new_maps.append((filename, opt))

    return new_maps

def cutsky(lonlat=None, maps=None, patch=[256, 1], coordframe='galactic', ctype=DEFAULT_ctype):
    """Old interface to cutsky -- Here mostly for compability

    Parameters
    ----------
    lonlat : array of 2 floats
        the longitude and latitude of the center of projection [deg]
    maps: a dict or a list
        either a dictionnary (old interface) or a list of tuple (new
        interface) :
        ```
        {legend: {'filename': full_filename_to_healpix_map.fits,
                  'doContour': True }, # optionnal
         ... }
         ```
         or
         ```
         [(full_filename_to_healpix_map.fits, {'legend': legend,
                                              'doContour': True}), # optionnal
         ... ]
         ```
    patch : array of [int, float]
        [int] the number of pixels and
        [float] the size of the pixel [arcmin]
    coordframe : str
        the coordinate frame used for the position AND the projection
    ctype: str
        a valid projection type (default: TAN)

    Returns
    -------
    list of dictionnaries
        the dictionnary has 4 keys :
        * 'legend' (see maps above),
        * 'fits' an ~astropy.io.fits.ImageHDU,
        * 'png', a b61encoded png image of the fits
        * 'phot', the corresponding photometry

    """

    if lonlat is None:
        raise ValueError("You must provide a lonlat argument")

    if maps is None:
        raise FileNotFoundError("No healpix map to project")

    if isinstance(maps, dict):
        maps = to_new_maps(maps)

    CutThoseMaps = CutSky(maps=maps, npix=patch[0], pixsize=patch[1], ctype=ctype)
    result = CutThoseMaps.cut_png(lonlat=lonlat, coordframe=coordframe)
    result = CutThoseMaps.cut_phot(lonlat=lonlat, coordframe=coordframe)

    return result

def parse_args(args):
    """Parse arguments from the command line"""

    parser = argparse.ArgumentParser(description="Reproject the spherical sky onto a plane.")
    parser.add_argument('lon', type=float,
                        help='longitude of the projection [deg]')
    parser.add_argument('lat', type=float,
                        help='latitude of the projection [deg]')

    # Removed the default argument from here otherwise the config file
    # wont be used

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--npix', type=int,
                        help='number of pixels (default 256)')
    group.add_argument('--radius', type=float,
                       help='radius of the requested region [deg] ')

    parser.add_argument('--pixsize', type=float,
                        help='pixel size [arcmin] (default 1)')

    parser.add_argument('--coordframe', required=False,
                        help='coordinate frame of the lon. and \
                        lat. of the projection and the projected map \
                        (default: galactic)',
                        choices=['galactic', 'fk5'])
    parser.add_argument('--ctype', required=False,
                        help='any projection code supported by wcslib\
                         (default:TAN)',
                        choices=VALID_PROJ)

    input_map = parser.add_argument_group('input maps', description="one of the two options must be present")

    input_map.add_argument('--mapfilenames', nargs='+', required=False,
                        help='absolute path to the healpix maps')
    input_map.add_argument('--conf', required=False,
                         help='absolute path to a config file')

    out = parser.add_argument_group('output')
    out.add_argument('--fits', action='store_true', help='output fits file')
    out.add_argument('--png', action='store_true', help='output png file (Default: True if nothing else)')
    out.add_argument('--votable', action='store_true', help='output votable file')
    out.add_argument('--outdir', required=False, help='output directory (default:".")')

    general = parser.add_argument_group('general')
    verb = general.add_mutually_exclusive_group()
    verb.add_argument('-v','--verbose', action='store_true', help='verbose mode')
    verb.add_argument('-q','--quiet', action='store_true', help='quiet mode')


    # Do the actual parsing
    parsed_args = parser.parse_args(args)

    # Put the list of filenames into the same structure as the config
    # file, we are loosing the doContour keyword but...
    if parsed_args.mapfilenames:
        parsed_args.maps = [ ( filename,
                               dict( [('legend', os.path.splitext(os.path.basename(filename))[0]) ]) )
                             for filename in
                             parsed_args.mapfilenames ]
    else:
        parsed_args.maps = None

    if parsed_args.verbose:
        parsed_args.verbosity = logging.DEBUG
    elif parsed_args.quiet:
        parsed_args.verbosity = logging.ERROR
    else:
        parsed_args.verbosity = None

    return parsed_args


def parse_config(conffile=None):
    """Parse options from a configuration file."""

    if pattern: #pragma: py2
       config = ConfigParser()
       def rep_key(m):
           section, key = re.split(':',m.group(1))
           return config.get(section,key)
    else: #pragma: py3
       config = ConfigParser(interpolation=ExtendedInterpolation())

    # Look for cutsky.cfg at several locations
    conffiles = [ os.path.join(directory, 'cutsky.cfg') for directory
                  in [os.curdir, os.path.join(os.path.expanduser("~"), '.config/cutsky')] ]

    # If specifically ask for a config file, then put it at the
    # beginning ...
    if conffile:
        conffiles.insert(0, conffile)

    found = config.read(conffiles)

    if not found:
        raise FileNotFoundError

    # Basic options, same as the option on the command line
    options = {}
    if config.has_option('cutsky','npix'):
        options['npix'] = config.getint('cutsky', 'npix')
    if config.has_option('cutsky', 'pixsize'):
        options['pixsize'] = config.getfloat('cutsky', 'pixsize')
    if config.has_option('cutsky', 'coordframe'):
        options['coordframe'] = config.get('cutsky', 'coordframe')
    if config.has_option('cutsky', 'ctype'):
        options['ctype'] = config.get('cutsky', 'ctype')
    if config.has_option('cutsky', 'verbosity'):
        level = config.get('cutsky', 'verbosity').lower()
        allowed_levels = { 'verbose': logging.DEBUG,
                           'debug': logging.DEBUG,
                           'quiet': logging.ERROR,
                           'error': logging.ERROR,
                           'info': logging.INFO}
        if level in allowed_levels.keys():
            options['verbosity'] = allowed_levels[level]
        else:
            try:
                options['verbosity'] = int(level)
            except ValueError:
                options['verbosity'] = None


    if config.has_option('cutsky', 'fits') and config.get('cutsky', 'fits'):
        options['fits'] = True
    if config.has_option('cutsky', 'png') and config.get('cutsky', 'png'):
        options['png'] = True
    if config.has_option('cutsky', 'votable') and config.get('cutsky', 'votable'):
        options['votable'] = True
    if config.has_option('cutsky', 'outdir'):
        options['outdir'] = config.get('cutsky','outdir')

    # Map list, only get the one which will be projected
    # Also check if contours are requested
    mapsToCut = []
    for section in config.sections():
        if section != 'cutsky':
            if config.has_option(section,'filename'):
                filename = config.get(section, 'filename')
                if pattern: #pragma: v2
                    filename = pattern.sub(rep_key, filename)
                # doCut options set to True if not present
                if ( config.has_option(section, 'doCut') and config.getboolean(section,'doCut')) or \
                   ( not config.has_option(section, 'doCut') ):
                    opt = {'legend': section}

                    if config.has_option(section, 'doContour'):
                        opt['doContour'] = config.getboolean(section, 'doContour')
                    mapsToCut.append((filename, opt))

    options['maps'] = mapsToCut

    return options

def combine_args(args, config):
    """
    Combine the different sources of arguments (command line,
    configfile or default arguments
    """

    # This is where the default arguments are set
    pixsize = args.pixsize or config.get('pixsize') or \
              DEFAULT_pixsize
    coordframe = args.coordframe or config.get('coordframe') or \
                 DEFAULT_coordframe
    ctype = args.ctype or config.get('ctype') or \
                 DEFAULT_ctype

    verbosity = args.verbosity or config.get('verbosity') or logging.INFO
    logger.setLevel(verbosity)

    # npix and radius are mutually exclusive, thus if radius is set we
    # need to compute npix
    if args.radius:
        args.npix = int(args.radius/(float(pixsize)/60))

    npix = args.npix or config.get('npix') or \
           DEFAULT_npix

    maps = args.maps or config.get('maps') or \
           None

    output = {}
    output['fits'] = args.fits or config.get('fits') or False
    output['png'] = args.png or config.get('png') or False
    output['votable'] = args.votable or config.get('votable') or False
    output['outdir'] = args.outdir or config.get('outdir') or '.'

    if not (output['fits'] or output['png'] or output['votable']):
        output['png'] = True

    return npix, pixsize, coordframe, ctype, maps, output


def main(argv = None):
    """The main routine."""

    if argv is None: # pragma: no cover
        argv = sys.argv[1:]

    from base64 import b64decode

    try:
        args = parse_args(argv)
    except SystemExit: # pragma: no cover
        sys.exit()

    try:
        config = parse_config(args.conf)
    except FileNotFoundError:
        config = {}

    npix, pixsize, coordframe, ctype, maps, output = combine_args(args, config)

    CutThoseMaps = CutSky(maps=maps, npix=npix, pixsize=pixsize, ctype=ctype)
    if output['fits']:
        results = CutThoseMaps.cut_fits(lonlat=[args.lon, args.lat], coordframe=coordframe)
    if output['png']:
        results = CutThoseMaps.cut_png(lonlat=[args.lon, args.lat], coordframe=coordframe)
    if output['votable']:
        results = CutThoseMaps.cut_phot(lonlat=[args.lon, args.lat], coordframe=coordframe)

    if not os.path.isdir(output['outdir']):
        os.makedirs(output['outdir'])

    for result in results:
        if 'fits' in result.keys() and output['fits']:
            try:
                hdulist = fits.HDUList([ fits.PrimaryHDU(), result['fits'] ])
                hdulist.writeto(os.path.join(output['outdir'],result['legend']+'.fits'), clobber=True)
            except NotImplementedError:
                result['fits'].data = result['fits'].data.filled()
                hdulist = fits.HDUList([ fits.PrimaryHDU(), result['fits'] ])
                hdulist.writeto(os.path.join(output['outdir'],result['legend']+'.fits'), clobber=True)

        if 'png' in result.keys() and output['png']:
            png = open(os.path.join(output['outdir'],result['legend']+'.png'), 'wb')
            png.write(b64decode(result['png']))
            png.close()

        if 'phot' in result.keys() and output['votable'] :
            result['phot'].write(os.path.join(output['outdir'],result['legend']+'.xml'), format='votable')

if __name__ == '__main__':
    main(sys.argv[1:])

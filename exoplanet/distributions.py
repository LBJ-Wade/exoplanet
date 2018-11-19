# -*- coding: utf-8 -*-

from __future__ import division, print_function

__all__ = ["UnitVector", "Angle", "RadiusImpactParameter"]

import numpy as np

import pymc3 as pm
from pymc3.distributions import draw_values, generate_samples

from . import transforms as tr
from .citations import add_citations_to_model


class UnitVector(pm.Normal):
    """A vector where the sum of squares is fixed to unity

    For a multidimensional shape, the normalization is performed along the
    last dimension.

    """

    def __init__(self, *args, **kwargs):
        kwargs["transform"] = tr.unit_vector
        super(UnitVector, self).__init__(*args, **kwargs)

    def _random(self, size=None):
        x = np.random.normal(size=size)
        return x / np.sqrt(np.sum(x**2, axis=-1, keepdims=True))

    def random(self, point=None, size=None):
        return generate_samples(self._random,
                                dist_shape=self.shape,
                                broadcast_shape=self.shape,
                                size=size)


class Angle(pm.Flat):
    """An angle constrained to be in the range -pi to pi

    The actual sampling is performed in the two dimensional vector space
    ``(sin(theta), cos(theta))`` so that the sampler doesn't see a
    discontinuity at pi.

    """

    def __init__(self, *args, **kwargs):
        kwargs["transform"] = tr.angle
        super(Angle, self).__init__(*args, **kwargs)
        self._default = np.zeros(self.shape)

    def _random(self, size=None):
        return np.random.uniform(-np.pi, np.pi, size)

    def random(self, point=None, size=None):
        return generate_samples(self._random,
                                dist_shape=self.shape,
                                broadcast_shape=self.shape,
                                size=size)


class Triangle(pm.Flat):
    """An uninformative prior for quadratic limb darkening parameters

    This is an implementation of the `Kipping (2013)
    <https://arxiv.org/abs/1308.0009>`_ reparameterization of the
    two-parameter limb darkening model to allow for efficient and
    uninformative sampling.

    """

    __citations__ = ("kipping13", )

    def __init__(self, *args, **kwargs):
        add_citations_to_model(self.__citations__, kwargs.get("model", None))

        # Make sure that the shape is compatible
        shape = kwargs.get("shape", 2)
        try:
            if list(shape)[0] != 2:
                raise ValueError("the first dimension should be exactly 2")
        except TypeError:
            if shape != 2:
                raise ValueError("the first dimension should be exactly 2")

        kwargs["shape"] = shape
        kwargs["transform"] = tr.triangle

        super(Triangle, self).__init__(*args, **kwargs)

        # Work out some reasonable starting values for the parameters
        default = np.zeros(shape)
        default[0] = np.sqrt(0.5)
        default[1] = 0.0
        self._default = default

    def _random(self, size=None):
        q = np.moveaxis(np.random.uniform(0, 1, size=size),
                        0, -len(self.shape))
        sqrtq1 = np.sqrt(q[0])
        twoq2 = 2 * q[1]
        u = np.stack([
            sqrtq1 * twoq2,
            sqrtq1 * (1 - twoq2),
        ], axis=0)
        return np.moveaxis(u, 0, -len(self.shape))

    def random(self, point=None, size=None):
        return generate_samples(self._random,
                                dist_shape=self.shape,
                                broadcast_shape=self.shape,
                                size=size)


class RadiusImpactParameter(pm.Flat):
    """The Espinoza (2018) distribution over radius and impact parameter

    This is an implementation of `Espinoza (2018)
    <http://iopscience.iop.org/article/10.3847/2515-5172/aaef38/meta>`_
    The first axis of the shape of the parameter should be exactly 2. The
    radius ratio will be in the zeroth entry in the first dimension and
    the impact parameter will be in the first.

    """
    __citations__ = ("espinoza18", )

    def __init__(self, *args, **kwargs):
        add_citations_to_model(self.__citations__, kwargs.get("model", None))

        # Make sure that the shape is compatible
        shape = kwargs.get("shape", 2)
        try:
            if list(shape)[0] != 2:
                raise ValueError("the first dimension should be exactly 2")
        except TypeError:
            if shape != 2:
                raise ValueError("the first dimension should be exactly 2")

        self.min_radius = kwargs.pop("min_radius", 0)
        self.max_radius = kwargs.pop("max_radius", 1)
        transform = tr.radius_impact(self.min_radius, self.max_radius)
        kwargs["shape"] = shape
        kwargs["transform"] = transform

        super(RadiusImpactParameter, self).__init__(*args, **kwargs)

        # Work out some reasonable starting values for the parameters
        default = np.zeros(shape)
        mn, mx = draw_values([self.min_radius-0., self.max_radius-0.])
        default[0] = 0.5 * (mn + mx)
        default[1] = 0.5
        self._default = default

    def _random(self, pl, pu, size=None):
        r = np.moveaxis(np.random.uniform(0, 1, size=size),
                        0, -len(self.shape))

        dr = pu - pl
        denom = 2 + pu + pl
        Ar = dr / denom

        r1 = r[0]
        r2 = r[1]
        m = r1 > Ar

        b = np.empty_like(r1)
        p = np.empty_like(r1)

        b[m] = (1 + pl) * (1 + (r1[m] - 1) / (1 - Ar))
        p[m] = pl + r2[m] * dr

        q1 = r1[~m] / Ar
        q2 = r2[~m]
        b[~m] = (1 + pl) + np.sqrt(q1) * q2 * dr
        p[~m] = pu - dr * np.sqrt(q1) * (1 - q2)

        pb = np.stack([p, b], axis=0)

        return np.moveaxis(pb, 0, -len(self.shape))

    def random(self, point=None, size=None):
        mn, mx = draw_values([self.min_radius-0., self.max_radius-0.],
                             point=point)
        return generate_samples(self._random, mn, mx,
                                dist_shape=self.shape,
                                broadcast_shape=self.shape,
                                size=size)


def get_joint_r_and_b_distribution(name="", N_planets=None,
                                   min_radius=0, max_radius=1,
                                   r_star=None, testval_r=None, testval_b=None,
                                   model=None, **kwargs):
    """Get the joint distribution over radius and impact parameter

    This uses the Espinoza (2018) parameterization of the distribution (see
    :class:`distributions.RadiusImpactParameter` for more details).

    Args:
        name (Optional[str]): A prefix that is added to all distribution names
            used in this parameterization. For example, if ``name`` is
            ``param_``, vars will be added to the PyMC3 model with names:
            ``param_rb`` (for the joint distribution), ``param_b``,
            ``param_r``, and optionally ``param_ror`` if ``r_star`` is given.
        N_planets (Optional[int]): The number of planets. If not provided, it
            will be inferred from the ``testval_*`` parameters or assumed to
            be 1.
        min_radius (Optional[float]): The minimum allowed radius.
        max_radius (Optional[float]): The maximum allowed radius.
        r_star (Optional[scalar]): The radius of the star (or a PyMC3 variable
            giving the stellar radius). If given, the radius parameter will be
            treated as a radius ratio instead of the physical radius.
        testval_r (Optional[float or array]): An initial guess for the radius
            parameter. This should be a ``float`` or an array with
            ``N_planets`` entries.
        testval_b (Optional[float or array]): An initial guess for the impact
            parameter. This should be a ``float`` or an array with
            ``N_planets`` entries.

    Returns:
        r (Deterministic): The planet radius parameter.
        b (Deterministic): The impact parameter.

    """
    if N_planets is None:
        if testval_r is not None:
            N_planets = len(np.atleast_1d(testval_r))
        elif testval_b is not None:
            N_planets = len(np.atleast_1d(testval_b))
        else:
            N_planets = 1
    N_planets = int(N_planets)

    # Set up the testval for the rb parameter
    rb_test = np.zeros((2, N_planets))
    if testval_r is None:
        rb_test[0, :] = 0.5 * (min_radius + max_radius)
    else:
        rb_test[0, :] = testval_r
    if testval_b is None:
        rb_test[1, :] = 0.5
    else:
        rb_test[1, :] = testval_b

    # Construct the join distribution
    rb = RadiusImpactParameter(
        name + "rb", min_radius=min_radius, max_radius=max_radius,
        shape=(2, N_planets), testval=rb_test, model=model, **kwargs)

    # Extract the individual components
    b = pm.Deterministic(name + "b", rb[1], model=model)

    # Determine if the radius parameter is the radius or the radius ratio
    if r_star is None:
        r = pm.Deterministic(name + "r", rb[0], model=model)
    else:
        ror = pm.Deterministic(name + "ror", rb[0])
        r = pm.Deterministic(name + "r", ror * r_star)

    return r, b

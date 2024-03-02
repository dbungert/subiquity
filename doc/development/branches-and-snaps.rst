
.. _branches_and_snaps:

Branches and Snaps
==================

The timing of the Subiquity stable release branches follows a 6 month cycle,
largely mirroring the release cycle of Ubuntu itself.

Mainline development
--------------------

Subiquity bugfixes and new features are expected to land first on the ``main``
branch.  Next, code changes are imported from GitHub to Launchpad by way of a
`scheduled job
<https://code.launchpad.net/~canonical-foundations/subiquity/+git/subiquity>`_
that runs every 6 hours. Snap builds from these ``main`` branch changes are
published the ``edge`` channel of the Subiquity snap.  Before feature freeze,
this ``edge`` snap is also auto-promoted to the ``stable/ubuntu-$yy.$mm``
channel, where ``$yy`` and ``$mm`` are the expected two-digit year and month of
the upcoming Ubuntu release, so that Ubuntu live-server ISOs are built with the
latest development build snap.

Feature Freeze and stable branches
----------------------------------

Stable branches are initially created around Ubuntu `feature freeze
<https://wiki.ubuntu.com/FeatureFreeze>`_ time, which is usually two months
before release.  The timing of this branch creation may delayed if all work on
the main branch is appropriate for the stable branch.  The stable branch is
created by branching from ``main`` to create ``ubuntu/$series``, where
``$series`` is the adjective of the upcoming Ubuntu release.  Snap builds from
the ``ubuntu/$series`` are published to the ``beta`` channel, and are
auto-promoted to ``stable/ubuntu-$yy.$mm``.

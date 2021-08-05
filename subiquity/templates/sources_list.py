template = '''\
# See http://help.ubuntu.com/community/UpgradeNotes for how to upgrade to
# newer versions of the distribution.
deb $MIRROR $RELEASE main restricted
# deb-src $MIRROR $RELEASE main restricted

## Major bug fix updates produced after the final release of the
## distribution.
deb $MIRROR $RELEASE-updates main restricted
# deb-src $MIRROR $RELEASE-updates main restricted

## N.B. software from this repository is ENTIRELY UNSUPPORTED by the Ubuntu
## team. Also, please note that software in universe WILL NOT receive any
## review or updates from the Ubuntu security team.
deb $MIRROR $RELEASE universe
# deb-src $MIRROR $RELEASE universe
deb $MIRROR $RELEASE-updates universe
# deb-src $MIRROR $RELEASE-updates universe

## N.B. software from this repository is ENTIRELY UNSUPPORTED by the Ubuntu
## team, and may not be under a free licence. Please satisfy yourself as to
## your rights to use the software. Also, please note that software in
## multiverse WILL NOT receive any review or updates from the Ubuntu
## security team.
deb $MIRROR $RELEASE multiverse
# deb-src $MIRROR $RELEASE multiverse
deb $MIRROR $RELEASE-updates multiverse
# deb-src $MIRROR $RELEASE-updates multiverse

## N.B. software from this repository may not have been tested as
## extensively as that contained in the main release, although it includes
## newer versions of some applications which may provide useful features.
## Also, please note that software in backports WILL NOT receive any review
## or updates from the Ubuntu security team.
deb $MIRROR $RELEASE-backports main restricted universe multiverse
# deb-src $MIRROR $RELEASE-backports main restricted universe multiverse

## Uncomment the following two lines to add software from Canonical's
## 'partner' repository.
## This software is not part of Ubuntu, but is offered by Canonical and the
## respective vendors as a service to Ubuntu users.
# deb http://archive.canonical.com/ubuntu $RELEASE partner
# deb-src http://archive.canonical.com/ubuntu $RELEASE partner

deb $SECURITY $RELEASE-security main restricted
# deb-src $SECURITY $RELEASE-security main restricted
deb $SECURITY $RELEASE-security universe
# deb-src $SECURITY $RELEASE-security universe
deb $SECURITY $RELEASE-security multiverse
# deb-src $SECURITY $RELEASE-security multiverse
'''

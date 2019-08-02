import os
import pwd

from osgtest.library import core
from osgtest.library import files
from osgtest.library.osgunittest import OSGTestCase
from osgtest.library import service


# These will end up as environment variables in the xrootd configs
# as well core.config["stashcache.KEY"] = var
# Xrootd config syntax doesn't allow underscores so this is CamelCase.
PARAMS = dict(
    CacheRootdir              = "/tmp/sccache",
    CacheXrootPort            = 1094,  # can't change this - stashcp doesn't allow you to specify port
    CacheHTTPPort             = 8001,
    CacheHTTPSPort            = 8444,
    OriginXrootPort           = 1095,
    OriginAuthXrootPort       = 1096,
    OriginRootdir             = "/tmp/scorigin",
    OriginExport              = "/osgtest/PUBLIC",
    OriginAuthExport          = "/osgtest/PROTECTED",
    OriginDummyExport         = "/osgtest/dummy",
    # ^ originexport needs to be defined on caches too because they use the same config.d
    #   This is relative to CacheRootdir, not OriginRootdir
    StashOriginAuthfile       = "/etc/xrootd/Authfile-origin",
    StashOriginPublicAuthfile = "/etc/xrootd/Authfile-origin-public",
    StashCacheAuthfile        = "/etc/xrootd/Authfile-cache",
    StashCachePublicAuthfile  = "/etc/xrootd/Authfile-cache-public",
    OriginResourcename        = "OSG_TEST_ORIGIN",
    CacheResourcename         = "OSG_TEST_CACHE",
)

PARAMS_CFG_PATH = "/etc/xrootd/config.d/01-params.cfg"
# Some statements can take env vars on the right hand side (setenv); others take config vars (set)
# so define both.
PARAMS_CFG_CONTENTS = "\n".join("setenv {0} = {1}\nset {0} = {1}".format(k, v)
                                for k, v in PARAMS.items()) + "\n"

PRE_CFG_PATH = "/etc/xrootd/config.d/11-pre.cfg"
PRE_CFG_CONTENTS = """
set DisableOsgMonitoring = 1

if named stash-cache-auth
    xrd.port $(CacheHTTPSPort)
    set rootdir = $(CacheRootdir)
    set resourcename = $(CacheResourcename)
    set originexport = $(OriginDummyExport)
    
    ofs.osslib libXrdPss.so
    pss.cachelib libXrdFileCache.so

    pss.origin localhost:$(OriginAuthXrootPort)
    xrd.protocol http:$(CacheHTTPSPort) libXrdHttp.so
else if named stash-cache
    xrd.port $(CacheXrootPort)
    set rootdir = $(CacheRootdir)
    set resourcename = $(CacheResourcename)
    set originexport = $(OriginDummyExport)

    ofs.osslib libXrdPss.so
    pss.cachelib libXrdFileCache.so

    pss.origin localhost:$(OriginXrootPort)
    xrd.protocol http:$(CacheHTTPPort) libXrdHttp.so
else if named stash-origin-auth
    xrd.port $(OriginAuthXrootPort)
    set rootdir = $(OriginRootdir)
    set resourcename = $(OriginResourcename)
    set originexport = $(OriginAuthExport)
else if named stash-origin
    xrd.port $(OriginXrootPort)
    set rootdir = $(OriginRootdir)
    set resourcename = $(OriginResourcename)
    set originexport = $(OriginExport)
fi
"""

CACHE_AUTHFILE_PATH = PARAMS["StashCacheAuthfile"]
CACHE_AUTHFILE_CONTENTS = "u * / rl\n"

CACHE_PUBLIC_AUTHFILE_PATH = PARAMS["StashCachePublicAuthfile"]
CACHE_PUBLIC_AUTHFILE_CONTENTS = "u * / rl\n"

ORIGIN_AUTHFILE_PATH = PARAMS["StashOriginAuthfile"]
ORIGIN_AUTHFILE_CONTENTS = "u * /osgtest/PROTECTED rl\n"

ORIGIN_PUBLIC_AUTHFILE_PATH = PARAMS["StashOriginPublicAuthfile"]
ORIGIN_PUBLIC_AUTHFILE_CONTENTS = "u * /osgtest/PUBLIC rl\n"

CACHES_JSON_PATH = "/etc/stashcache/caches.json"
CACHES_JSON_CONTENTS = """\
[
{"name":"root://localhost", "status":1, "longitude":-89.4012, "latitude":43.0731}
]
"""

XROOTD_ORIGIN_CFG_PATH = "/etc/xrootd/xrootd-stash-origin.cfg"
HTTP_CFG_PATH = "/etc/xrootd/config.d/40-osg-http.cfg"
if core.PackageVersion('stash-cache') >= '1.1.0':
    CACHING_PLUGIN_CFG_PATH = "/etc/xrootd/config.d/40-stash-cache-plugin.cfg"
else:
    CACHING_PLUGIN_CFG_PATH = "/etc/xrootd/config.d/40-osg-caching-plugin.cfg"

NAMESPACE = "stashcache"


def setcfg(key, val):
    core.config["%s.%s" % (NAMESPACE, key)] = val


def start_xrootd(instance):
    svc = "xrootd@%s" % instance
    if not service.is_running(svc):
        service.check_start(svc)


class TestStartStashCache(OSGTestCase):
    @core.elrelease(7,8)
    def setUp(self):
        core.skip_ok_unless_installed("stash-origin",
                                      "stash-cache",
                                      "stashcache-client",
                                      by_dependency=True)
        if core.rpm_is_installed("xcache"):
            self.skip_ok_if(core.PackageVersion("xcache") < "1.0.2", "needs xcache 1.0.2+")

    def test_01_configure(self):
        for key, val in PARAMS.items():
            setcfg(key, val)

        # Create dirs
        for d in [PARAMS["OriginRootdir"],
                  PARAMS["CacheRootdir"],
                  os.path.join(PARAMS["OriginRootdir"], PARAMS["OriginExport"].lstrip("/")),
                  os.path.join(PARAMS["OriginRootdir"], PARAMS["OriginAuthExport"].lstrip("/")),
                  os.path.join(PARAMS["CacheRootdir"], PARAMS["OriginDummyExport"].lstrip("/")),
                  os.path.dirname(CACHES_JSON_PATH)]:
            files.safe_makedirs(d)

        core.system(["chown", "-R", "xrootd:xrootd", PARAMS["OriginRootdir"], PARAMS["CacheRootdir"]])

        filelist = []
        setcfg("filelist", filelist)
        # Modify filelist in-place with .append so changes get into core.config too

        # Delete the lines we can't override
        for path, regexp in [
            (XROOTD_ORIGIN_CFG_PATH, "^\s*all.manager.+$"),
            (HTTP_CFG_PATH, "^\s*xrd.protocol.+$"),
            (CACHING_PLUGIN_CFG_PATH, "^\s*(ofs.osslib|pss.cachelib|pss.origin).+$"),
        ]:
            files.replace_regexpr(path, regexp, "", owner=NAMESPACE)
            filelist.append(path)

        # Write our new files
        for path, contents in [
            (PARAMS_CFG_PATH, PARAMS_CFG_CONTENTS),
            (PRE_CFG_PATH, PRE_CFG_CONTENTS),
            (ORIGIN_AUTHFILE_PATH, ORIGIN_AUTHFILE_CONTENTS),
            (ORIGIN_PUBLIC_AUTHFILE_PATH, ORIGIN_PUBLIC_AUTHFILE_CONTENTS),
            (CACHE_AUTHFILE_PATH, CACHE_AUTHFILE_CONTENTS),
            (CACHE_PUBLIC_AUTHFILE_PATH, CACHE_PUBLIC_AUTHFILE_CONTENTS),
            (CACHES_JSON_PATH, CACHES_JSON_CONTENTS)
        ]:
            files.write(path, contents, owner=NAMESPACE, chmod=0o644)
            filelist.append(path)

        # Install certs.  Normally done in the xrootd tests but they conflict with the StashCache tests
        # (both use the same config dir)
        core.config['certs.xrootdcert'] = '/etc/grid-security/xrd/xrdcert.pem'
        core.config['certs.xrootdkey'] = '/etc/grid-security/xrd/xrdkey.pem'
        core.install_cert('certs.xrootdcert', 'certs.hostcert', 'xrootd', 0o644)
        core.install_cert('certs.xrootdkey', 'certs.hostkey', 'xrootd', 0o400)

    def test_02_start_stash_origin(self):
        start_xrootd("stash-origin")

    def test_03_start_stash_origin_auth(self):
        start_xrootd("stash-origin-auth")

    def test_04_start_stash_cache(self):
        start_xrootd("stash-cache")

    def test_05_start_stash_cache_auth(self):
        start_xrootd("stash-cache-auth")

from argparse import ArgumentParser
from copy import copy
from hashlib import sha1
from io import BytesIO, StringIO
import logging
import math
from math import exp, log
import os
import pkgutil
import sys
from typing import List, Optional, Union, cast
from urllib.parse import urlencode, urljoin

from PIL import Image
import botocore.exceptions
from bottle import jinja2_template
from c2cwsgiutils import stats
import requests
import ruamel.yaml

from tilecloud.lib.PIL_ import FORMAT_BY_CONTENT_TYPE
import tilecloud.store.redis
import tilecloud.store.s3
from tilecloud_chain import TileGeneration, add_comon_options, get_queue_store, get_tile_matrix_identifier
import tilecloud_chain.configuration

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        stats.init_backends({})
        parser = ArgumentParser(
            description="Used to generate the contextual file like the capabilities, the legends, "
            "the Apache and MapCache configuration",
            prog=sys.argv[0],
        )
        add_comon_options(parser, tile_pyramid=False, no_geom=False)
        parser.add_argument(
            "--status", default=False, action="store_true", help="Display the SQS queue status and exit"
        )
        parser.add_argument(
            "--capabilities",
            "--generate-wmts-capabilities",
            default=False,
            action="store_true",
            help="Generate the WMTS Capabilities",
        )
        parser.add_argument(
            "--legends",
            "--generate-legend-images",
            default=False,
            action="store_true",
            dest="legends",
            help="Generate the legend images",
        )
        parser.add_argument(
            "--openlayers",
            "--generate-openlayers-testpage",
            default=False,
            action="store_true",
            dest="openlayers",
            help="Generate openlayers test page",
        )
        parser.add_argument(
            "--mapcache",
            "--generate-mapcache-config",
            default=False,
            action="store_true",
            dest="mapcache",
            help="Generate MapCache configuration file",
        )
        parser.add_argument(
            "--mapcache-version", default="1.4", choices=("1.4", "1.6"), help="The used version of MapCache"
        )
        parser.add_argument(
            "--apache",
            "--generate-apache-config",
            default=False,
            action="store_true",
            dest="apache",
            help="Generate Apache configuration file",
        )
        parser.add_argument(
            "--dump-config",
            default=False,
            action="store_true",
            help="Dump the used config with default values and exit",
        )

        options = parser.parse_args()
        gene = TileGeneration(options.config, options, layer_name=options.layer)

        if options.status:
            status(gene)
            sys.exit(0)

        if options.cache is None:
            options.cache = gene.config["generation"]["default_cache"]

        if options.dump_config:
            _validate_generate_wmts_capabilities(gene.caches[options.cache], options.cache, True)
            yaml = ruamel.yaml.YAML()  # type: ignore
            out = StringIO()
            yaml.dump(gene.config, out)
            print(out.getvalue())
            sys.exit(0)

        if options.legends:
            _generate_legend_images(gene)

        if options.capabilities:
            _generate_wmts_capabilities(gene)

        if options.mapcache:
            _generate_mapcache_config(gene, options.mapcache_version)

        if options.apache:
            _generate_apache_config(gene)

        if options.openlayers:
            _generate_openlayers(gene)
    except SystemExit:
        raise
    except:  # pylint: disable=bare-except
        logger.exception("Exit with exception")
        if os.environ.get("TESTS", "false").lower() == "true":
            raise
        sys.exit(1)


def _send(
    data: Union[bytes, str], path: str, mime_type: str, cache: tilecloud_chain.configuration.Cache
) -> None:
    if cache["type"] == "s3":
        cache_s3 = cast(tilecloud_chain.configuration.CacheS3, cache)
        client = tilecloud.store.s3.get_client(cache_s3.get("host"))
        key_name = os.path.join("{folder!s}".format(**cache), path)
        bucket = cache_s3["bucket"]
        client.put_object(
            ACL="public-read",
            Body=data,
            Key=key_name,
            Bucket=bucket,
            ContentEncoding="utf-8",
            ContentType=mime_type,
        )
    else:
        if isinstance(data, str):
            data = data.encode("utf-8")

        folder = cache["folder"] or ""
        filename = os.path.join(folder, path)
        directory = os.path.dirname(filename)
        if not os.path.exists(directory):
            os.makedirs(directory)
        with open(filename, "wb") as f:
            f.write(data)


def _get(path: str, cache: tilecloud_chain.configuration.Cache) -> Optional[bytes]:
    if cache["type"] == "s3":
        cache_s3 = cast(tilecloud_chain.configuration.CacheS3, cache)
        client = tilecloud.store.s3.get_client(cache_s3.get("host"))
        key_name = os.path.join("{folder}".format(**cache), path)
        bucket = cache_s3["bucket"]
        try:
            response = client.get_object(Bucket=bucket, Key=key_name)
            return cast(bytes, response["Body"].read())
        except botocore.exceptions.ClientError as ex:
            if ex.response["Error"]["Code"] == "NoSuchKey":
                return None
            else:
                raise
    else:
        cache_filesystem = cast(tilecloud_chain.configuration.CacheFilesystem, cache)
        p = os.path.join(cache_filesystem["folder"], path)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as file:
            return file.read()


def _validate_generate_wmts_capabilities(
    cache: tilecloud_chain.configuration.Cache, cache_name: str, exit_: bool
) -> bool:
    if "http_url" not in cache and "http_urls" not in cache:
        logger.error(
            "The attribute 'http_url' or 'http_urls' is required in the object cache[%s].", cache_name
        )
        if exit_:
            sys.exit(1)
        return False
    return True


def get_wmts_capabilities(gene: TileGeneration, cache_name: str, exit_: bool = False) -> Optional[str]:
    cache = gene.config["caches"][cache_name]
    if _validate_generate_wmts_capabilities(cache, cache_name, exit_):
        server = gene.config.get("server")

        base_urls = _get_base_urls(cache)
        _fill_legend(gene, cache, server, base_urls)

        data = pkgutil.get_data("tilecloud_chain", "wmts_get_capabilities.jinja")
        assert data
        return cast(
            str,
            jinja2_template(
                data.decode("utf-8"),
                layers=gene.layers,
                layer_legends=gene.layer_legends,
                grids=gene.grids,
                getcapabilities=urljoin(  # type: ignore
                    base_urls[0],
                    (
                        server.get("wmts_path", "wmts") + "/1.0.0/WMTSCapabilities.xml"
                        if server is not None
                        else cache.get("wmtscapabilities_file", "1.0.0/WMTSCapabilities.xml")
                    ),
                ),
                base_urls=base_urls,
                base_url_postfix=(server.get("wmts_path", "wmts") + "/") if server is not None else "",
                get_tile_matrix_identifier=get_tile_matrix_identifier,
                server=server is not None,
                has_metadata=gene.metadata is not None,
                metadata=gene.metadata,
                has_provider=gene.provider is not None,
                provider=gene.provider,
                enumerate=enumerate,
                ceil=math.ceil,
                int=int,
                sorted=sorted,
            ),
        )
    return None


def _get_base_urls(cache: tilecloud_chain.configuration.Cache) -> List[str]:
    base_urls = []
    if "http_url" in cache:
        if "hosts" in cache:
            cc = copy(cache)
            for host in cache["hosts"]:
                cc["host"] = host  # type: ignore
                base_urls.append(cache["http_url"] % cc)
        else:
            base_urls = [cache["http_url"] % cache]
    if "http_urls" in cache:
        base_urls = [url % cache for url in cache["http_urls"]]
    base_urls = [url + "/" if url[-1] != "/" else url for url in base_urls]
    return base_urls


def _fill_legend(
    gene: TileGeneration,
    cache: tilecloud_chain.configuration.Cache,
    server: Optional[tilecloud_chain.configuration.Server],
    base_urls: List[str],
) -> None:
    for layer_name, layer in gene.layers.items():
        previous_legend: Optional[tilecloud_chain.Legend] = None
        previous_resolution = None
        if "legend_mime" in layer and "legend_extension" in layer and layer_name not in gene.layer_legends:
            gene.layer_legends[layer_name] = []
            legends = gene.layer_legends[layer_name]
            for zoom, resolution in enumerate(gene.config["grids"][layer["grid"]]["resolutions"]):
                path = "/".join(
                    [
                        "1.0.0",
                        layer_name,
                        layer["wmts_style"],
                        "legend{}.{}".format(zoom, layer["legend_extension"]),
                    ]
                )
                img = _get(path, cache)
                if img is not None:
                    new_legend: tilecloud_chain.Legend = {
                        "mime_type": layer["legend_mime"],
                        "href": os.path.join(
                            base_urls[0], server.get("static_path", "static") + "/" if server else "", path
                        ),
                    }
                    legends.append(new_legend)
                    if previous_legend is not None:
                        assert previous_resolution is not None
                        middle_res = exp((log(previous_resolution) + log(resolution)) / 2)
                        previous_legend[  # pylint: disable=unsupported-assignment-operation
                            "min_resolution"
                        ] = middle_res
                        new_legend["max_resolution"] = middle_res
                    try:
                        pil_img = Image.open(BytesIO(img))
                        new_legend["width"] = pil_img.size[0]
                        new_legend["height"] = pil_img.size[1]
                    except Exception:  # pragma: nocover
                        logger.warning(
                            "Unable to read legend image '%s', with '%s'",
                            path,
                            repr(img),
                            exc_info=True,
                        )
                    previous_legend = new_legend
                previous_resolution = resolution


def _generate_wmts_capabilities(gene: TileGeneration) -> None:
    cache = cast(tilecloud_chain.configuration.CacheS3, gene.caches[gene.options.cache])

    capabilities = get_wmts_capabilities(gene, gene.options.cache, True)
    assert capabilities is not None
    _send(
        capabilities,
        cache.get("wmtscapabilities_file", "1.0.0/WMTSCapabilities.xml"),
        "application/xml",
        cache,
    )


def _generate_legend_images(gene: TileGeneration) -> None:
    cache = gene.caches[gene.options.cache]

    for layer_name, layer in gene.layers.items():
        if "legend_mime" in layer and "legend_extension" in layer:
            if layer["type"] == "wms":
                session = requests.session()
                session.headers.update(layer["headers"])
                previous_hash = None
                for zoom, resolution in enumerate(gene.config["grids"][layer["grid"]]["resolutions"]):
                    legends = []
                    for wmslayer in layer["layers"].split(","):
                        response = session.get(
                            layer["url"]
                            + "?"
                            + urlencode(
                                {
                                    "SERVICE": "WMS",
                                    "VERSION": layer.get("version", "1.0.0"),
                                    "REQUEST": "GetLegendGraphic",
                                    "LAYER": wmslayer,
                                    "FORMAT": layer["legend_mime"],
                                    "TRANSPARENT": "TRUE" if layer["legend_mime"] == "image/png" else "FALSE",
                                    "STYLE": layer["wmts_style"],
                                    "SCALE": resolution / 0.00028,
                                }
                            )
                        )
                        try:
                            legends.append(Image.open(BytesIO(response.content)))
                        except Exception:  # pragma: nocover
                            logger.warning(
                                "Unable to read legend image for layer '%s'-'%s', resolution '%s': %s",
                                layer_name,
                                wmslayer,
                                resolution,
                                response.content,
                                exc_info=True,
                            )
                    width = max(i.size[0] for i in legends)
                    height = sum(i.size[1] for i in legends)
                    image = Image.new("RGBA", (width, height))
                    y = 0
                    for i in legends:
                        image.paste(i, (0, y))
                        y += i.size[1]
                    string_io = BytesIO()
                    image.save(string_io, FORMAT_BY_CONTENT_TYPE[layer["legend_mime"]])
                    result = string_io.getvalue()
                    new_hash = sha1(result).hexdigest()
                    if new_hash != previous_hash:
                        previous_hash = new_hash
                        _send(
                            result,
                            "1.0.0/{}/{}/legend{}.{}".format(
                                layer_name, layer["wmts_style"], zoom, layer["legend_extension"]
                            ),
                            layer["legend_mime"],
                            cache,
                        )


def _generate_mapcache_config(gene: TileGeneration, version: str) -> None:
    for layer in gene.layers.values():
        if layer["type"] == "wms" or "wms_url" in layer:
            layer_wms = cast(tilecloud_chain.configuration.LayerWms, layer)
            params = {}
            params.update(layer_wms.get("params", {}))
            if "FORMAT" not in params:
                params["FORMAT"] = layer_wms["mime_type"]
            if "LAYERS" not in layer_wms.get("params", {}):
                params["LAYERS"] = layer_wms["layers"]
            if "TRANSPARENT" not in layer_wms.get("params", {}):
                params["TRANSPARENT"] = "TRUE" if layer_wms["mime_type"] == "image/png" else "FALSE"
            layer_wms["params"] = params

    data = pkgutil.get_data("tilecloud_chain", "mapcache_config.jinja")
    assert data
    config = jinja2_template(
        data.decode("utf-8"),
        layers=gene.layers,
        grids=gene.grids,
        mapcache=gene.config["mapcache"],
        version=version,
        min=min,
        len=len,
        sorted=sorted,
    )

    with open(gene.config["mapcache"]["config_file"], "w") as f:
        f.write(config)


def _generate_apache_config(gene: TileGeneration) -> None:
    cache = gene.caches[gene.options.cache]
    use_server = "server" in gene.config

    with open(gene.config["apache"]["config_file"], "w") as f:

        folder = cache["folder"]
        if folder and folder[-1] != "/":
            folder += "/"

        if not use_server:
            f.write(
                """
    <Location {location}>
        ExpiresActive on
        ExpiresDefault "now plus {expires} hours"
    {headers}
    </Location>
    """.format(
                    **{
                        "location": gene.config["apache"]["location"],
                        "expires": gene.config["apache"]["expires"],
                        "headers": "".join(
                            [
                                '    Header set {} "{}"'.format(*h)
                                for h in gene.config["apache"]
                                .get("headers", {"Cache-Control": "max-age=864000, public"})
                                .items()
                            ]
                        ),
                    }
                )
            )
            if cache["type"] == "s3":
                cache_s3 = cast(tilecloud_chain.configuration.CacheS3, cache)
                tiles_url = (
                    (cache_s3["tiles_url"] % cache)
                    if "tiles_url" in cache
                    else "http://s3-{region}.amazonaws.com/{bucket}/{folder}".format(
                        **{
                            "region": cache_s3.get("region", "eu-west-1"),
                            "bucket": cache_s3["bucket"],
                            "folder": folder,
                        }
                    )
                )
                f.write(
                    """
    <Proxy {tiles_url}*>
        Order deny,allow
        Allow from all
    </Proxy>
    ProxyPass {location}/ {tiles_url}
    ProxyPassReverse {location}/ {tiles_url}
    """.format(
                        **{"location": gene.config["apache"]["location"], "tiles_url": tiles_url}
                    )
                )
            elif cache["type"] == "filesystem":
                f.write(
                    """
    Alias {location!s} {files_folder!s}
    """.format(
                        **{
                            "location": gene.config["apache"]["location"],
                            "files_folder": folder,
                            "headers": "".join(
                                [
                                    "    Header set {} '{}'".format(*h)
                                    for h in gene.config["apache"]
                                    .get("headers", {"Cache-Control": "max-age=864000, public"})
                                    .items()
                                ]
                            ),
                        }
                    )
                )

        use_mapcache = "mapcache" in gene.config
        if use_mapcache and not use_server:
            token_regex = r"([a-zA-Z0-9_\-\+~\.]+)"
            f.write("\n")

            for layer_name, layer in sorted(gene.config["layers"].items()):
                if "min_resolution_seed" in layer:
                    res = [
                        r
                        for r in gene.config["grids"][layer["grid"]]["resolutions"]
                        if r < layer["min_resolution_seed"]
                    ]
                    dim = len(layer.get("dimensions", []))
                    for r in res:
                        f.write(
                            "RewriteRule"
                            " "
                            "^%(tiles_location)s/1.0.0/%(layer)s/%(token_regex)s"  # Baseurl/layer/Style
                            "%(dimensions_re)s"  # Dimensions : variable number of values
                            # TileMatrixSet/TileMatrix/TileRow/TileCol.extension
                            "/%(token_regex)s/%(zoom)s/(.*)$"
                            " "
                            "%(mapcache_location)s/wmts/1.0.0/%(layer)s/$1"
                            "%(dimensions_rep)s"
                            "/$%(tilematrixset)s/%(zoom)s/$%(final)s"
                            " "
                            "[PT]\n"
                            % {
                                "tiles_location": gene.config["apache"]["location"],
                                "mapcache_location": gene.config["mapcache"]["location"],
                                "layer": layer_name,
                                "token_regex": token_regex,
                                "dimensions_re": "".join(["/" + token_regex for e in range(dim)]),
                                "dimensions_rep": "".join([f"/${e + 2}" for e in range(dim)]),
                                "tilematrixset": dim + 2,
                                "final": dim + 3,
                                "zoom": gene.config["grids"][layer["grid"]]["resolutions"].index(r),
                            }
                        )

        if use_mapcache:
            f.write(
                """
    MapCacheAlias {mapcache_location!s} "{mapcache_config!s}"
    """.format(
                    **{
                        "mapcache_location": gene.config["mapcache"]["location"],
                        "mapcache_config": os.path.abspath(gene.config["mapcache"]["config_file"]),
                    }
                )
            )


def _get_resource(resource: str) -> bytes:
    path = os.path.join(os.path.dirname(__file__), resource)
    with open(path, "rb") as f:
        return f.read()


def _generate_openlayers(gene: TileGeneration) -> None:
    cache = gene.caches[gene.options.cache]

    http_url = ""
    if "http_url" in cache and cache["http_url"]:
        if "hosts" in cache and cache["hosts"]:
            cc = copy(cache)
            cc["host"] = cache["hosts"][0]  # type: ignore
            http_url = cache["http_url"] % cc
        else:
            http_url = cache["http_url"] % cache
    if "http_urls" in cache and cache["http_urls"]:
        http_url = cache["http_urls"][0] % cache

    data = pkgutil.get_data("tilecloud_chain", "openlayers.js")
    assert data
    js = jinja2_template(
        data.decode("utf-8"),
        srs=gene.config["openlayers"]["srs"],
        center_x=gene.config["openlayers"]["center_x"],
        center_y=gene.config["openlayers"]["center_y"],
        http_url=http_url
        + (gene.config["server"].get("wmts_path", "wmts") + "/" if "server" in gene.config else ""),
        layers=[
            {
                "name": name,
                "grid": layer["type"] == "mapnik"
                and (layer.get("output_format", "png") == tilecloud_chain.configuration.OUTPUTFORMAT_GRID),
                "maxExtent": gene.config["grids"][layer["grid"]]["bbox"],
                "resolution": layer.get("resolution", 4)
                if layer["type"] == "mapnik"
                and layer.get("output_format", "png") == tilecloud_chain.configuration.OUTPUTFORMAT_GRID
                else None,
            }
            for name, layer in sorted(gene.layers.items())
            if gene.config["grids"][layer["grid"]]["srs"] == gene.config["openlayers"]["srs"]
        ],
        sorted=sorted,
    )

    html = pkgutil.get_data("tilecloud_chain", "openlayers.html")
    assert html
    _send(html, "index.html", "text/html", cache)
    _send(js, "wmts.js", "application/javascript", cache)
    _send(_get_resource("OpenLayers.js"), "OpenLayers.js", "application/javascript", cache)
    _send(_get_resource("OpenLayers-style.css"), "theme/default/style.css", "text/css", cache)
    _send(_get_resource("layer-switcher-maximize.png"), "img/layer-switcher-maximize.png", "image/png", cache)
    _send(_get_resource("layer-switcher-minimize.png"), "img/layer-switcher-minimize.png", "image/png", cache)


def status(gene: TileGeneration) -> None:
    print("\n".join(get_status(gene)))


def get_status(gene: TileGeneration) -> List[str]:
    store = get_queue_store(gene.config, False)
    prefix = "redis" if "redis" in gene.config else "sqs"
    conf = gene.config["redis"] if "redis" in gene.config else gene.config["sqs"]
    stats_prefix = [prefix, conf["queue"]]
    with stats.timer_context(stats_prefix + ["get_stats"]):
        status_ = store.get_status()
    return [name + ": " + str(value) for name, value in status_.items()]
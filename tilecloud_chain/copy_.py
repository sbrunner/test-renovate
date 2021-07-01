from argparse import ArgumentParser, Namespace
import logging
import os
import sys
from typing import cast

from tilecloud_chain import Count, DropEmpty, HashDropper, TileGeneration, add_comon_options
import tilecloud_chain.configuration
from tilecloud_chain.format import duration_format, size_format

logger = logging.getLogger(__name__)


class Copy:
    count = None

    def copy(
        self,
        options: Namespace,
        gene: TileGeneration,
        layer: str,
        source: str,
        destination: str,
        task_name: str,
    ) -> None:
        if gene.layers[layer]["type"] == "wms":
            self._copy(options, gene, layer, source, destination, task_name)
        else:
            self._copy(options, gene, layer, source, destination, task_name)

    def _copy(
        self,
        options: Namespace,
        gene: TileGeneration,
        layer_name: str,
        source: str,
        dest: str,
        task_name: str,
    ) -> None:
        # disable metatiles
        layer = gene.layers[layer_name]
        cast(tilecloud_chain.configuration.LayerWms, layer)["meta"] = False
        count_tiles_dropped = Count()

        gene.init_layer(layer_name, options)
        source_tilestore = gene.get_tilesstore(source)
        dest_tilestore = gene.get_tilesstore(dest)
        gene.init_tilecoords(layer_name)
        gene.add_geom_filter()
        gene.add_logger()
        gene.get(source_tilestore, "Get the tiles")
        gene.imap(DropEmpty(gene))
        # Discard tiles with certain content
        if "empty_tile_detection" in layer:
            empty_tile = layer["empty_tile_detection"]

            gene.imap(
                HashDropper(
                    empty_tile["size"], empty_tile["hash"], store=dest_tilestore, count=count_tiles_dropped
                )
            )

        if options.process:
            gene.process(options.process)

        gene.imap(DropEmpty(gene))
        self.count = gene.counter_size()
        gene.put(dest_tilestore, "Store the tiles")
        gene.consume()
        if not options.quiet:
            print(
                """The tile {} of layer '{}' is finish
Nb {} tiles: {}
Nb errored tiles: {}
Nb dropped tiles: {}
Total time: {}
Total size: {}
Time per tile: {} ms
Size per tile: {} o
""".format(
                    task_name,
                    layer_name,
                    task_name,
                    self.count.nb,
                    count_tiles_dropped.nb,
                    gene.error,
                    duration_format(gene.duration),
                    size_format(self.count.size),
                    (gene.duration / self.count.nb * 1000).seconds if self.count.nb != 0 else 0,
                    self.count.size / self.count.nb if self.count.nb != 0 else -1,
                )
            )


def main() -> None:
    try:
        parser = ArgumentParser(
            description="Used to copy the tiles from a cache to an other", prog=sys.argv[0]
        )
        add_comon_options(parser, near=False, time=False, dimensions=True, cache=False)
        parser.add_argument("--process", dest="process", metavar="NAME", help="The process name to do")
        parser.add_argument("source", metavar="SOURCE", help="The source cache")
        parser.add_argument("dest", metavar="DEST", help="The destination cache")

        options = parser.parse_args()

        gene = TileGeneration(options.config, options)

        if options.layer:
            copy = Copy()
            copy.copy(options, gene, options.layer, options.source, options.dest, "copy")
        else:
            layers = (
                gene.config["generation"]["default_layers"]
                if "default_layers" in gene.config["generation"]
                else gene.config["layers"].keys()
            )
            for layer in layers:
                copy = Copy()
                copy.copy(options, gene, layer, options.source, options.dest, "copy")
    except SystemExit:
        raise
    except:  # pylint: disable=bare-except
        logger.exception("Exit with exception")
        if os.environ.get("TESTS", "false").lower() == "true":
            raise
        sys.exit(1)


def process() -> None:
    try:
        parser = ArgumentParser(
            description="Used to copy the tiles from a cache to an other", prog=sys.argv[0]
        )
        add_comon_options(parser, near=False, time=False, dimensions=True)
        parser.add_argument("process", metavar="PROCESS", help="The process name to do")

        options = parser.parse_args()

        gene = TileGeneration(options.config, options, multi_thread=False)

        copy = Copy()
        if options.layer:
            copy.copy(options, gene, options.layer, options.cache, options.cache, "process")
        else:
            layers_name = (
                gene.config["generation"]["default_layers"]
                if "default_layers" in gene.config.get("generation", {})
                else gene.layers.keys()
            )
            for layer in layers_name:
                copy.copy(options, gene, layer, options.cache, options.cache, "process")
    except SystemExit:
        raise
    except:  # pylint: disable=bare-except
        logger.exception("Exit with exception")
        sys.exit(1)

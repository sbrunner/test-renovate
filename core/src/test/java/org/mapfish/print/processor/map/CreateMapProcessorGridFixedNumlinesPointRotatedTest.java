package org.mapfish.print.processor.map;

import org.junit.Test;
import org.mapfish.print.AbstractMapfishSpringTest;
import org.mapfish.print.TestHttpClientFactory;
import org.mapfish.print.config.Configuration;
import org.mapfish.print.config.ConfigurationFactory;
import org.mapfish.print.config.Template;
import org.mapfish.print.output.Values;
import org.mapfish.print.test.util.ImageSimilarity;
import org.mapfish.print.wrapper.json.PJsonObject;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.annotation.DirtiesContext;

import java.io.File;
import java.io.IOException;
import java.net.URI;
import java.util.List;
import java.util.concurrent.ForkJoinPool;
import java.util.concurrent.ForkJoinTask;

import static org.junit.Assert.assertEquals;

/**
 * Basic test of the Map processor.
 *
 * Created by Jesse on 3/26/14.
 */
public class CreateMapProcessorGridFixedNumlinesPointRotatedTest extends AbstractMapfishSpringTest {
    public static final String BASE_DIR = "grid_numlines_points_fixedscale_rotated/";

    @Autowired
    private ConfigurationFactory configurationFactory;
    @Autowired
    private TestHttpClientFactory requestFactory;
    @Autowired
    private ForkJoinPool forkJoinPool;

    private static PJsonObject loadJsonRequestData() throws IOException {
        return parseJSONObjectFromFile(CreateMapProcessorGridFixedNumlinesPointRotatedTest.class,
                                       BASE_DIR + "requestData.json");
    }

    @Test
    @DirtiesContext
    public void testExecute() throws Exception {
        final String host = "center_osm_fixedscale";
        requestFactory.registerHandler(
                input -> (("" + input.getHost()).contains(host + ".osm")) ||
                        input.getAuthority().contains(host + ".osm"),
                createFileHandler(uri -> "/map-data/osm" + uri.getPath())

        );

        final Configuration config = configurationFactory.getConfig(getFile(BASE_DIR + "config.yaml"));
        final Template template = config.getTemplate("main");
        PJsonObject requestData = loadJsonRequestData();
        PJsonObject map = requestData.getJSONObject("attributes").getJSONObject("map");
        int[] rotationsToTest = {23, 90, 123, 180, 203, 270, 310, 360};

        for (int rotation: rotationsToTest) {
            map.getInternalObj().put("rotation", rotation);
            Values values = new Values("test", requestData, template, getTaskDirectory(), this.requestFactory,
                                       new File("."));

            final ForkJoinTask<Values> taskFuture = this.forkJoinPool.submit(
                    template.getProcessorGraph().createTask(values));
            taskFuture.get();

            @SuppressWarnings("unchecked")
            List<URI> layerGraphics = (List<URI>) values.getObject("layerGraphics", List.class);
            assertEquals(2, layerGraphics.size());

            String imageName = getExpectedImageName("_" + rotation, BASE_DIR);
            new ImageSimilarity(getFile(BASE_DIR + imageName)).assertSimilarity(layerGraphics, 780, 330, 200);
        }
    }
}
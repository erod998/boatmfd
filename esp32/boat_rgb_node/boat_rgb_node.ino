// ESP32 companion node: drives a WS2812B strip over WiFi and exposes the
// same lighting API shape the RPi dashboard uses, so it can be a drop-in
// lighting node (or the whole POC can run on ESP32 alone with the RPi
// dashboard omitted, at the cost of the map/chart UI, which needs more
// RAM/flash than is comfortable on this chip).
//
// Libraries needed (Arduino IDE > Library Manager):
//   - Adafruit NeoPixel
//   - ArduinoJson
//   - WiFi (bundled with the ESP32 board package)
//
// Wiring: LED strip DIN -> GPIO 5 (via a 330-470ohm resistor), strip 5V/GND
// from a supply sized for the strip (not the ESP32 3V3 pin), plus a common
// ground between the ESP32 and the strip's power supply.

#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <Adafruit_NeoPixel.h>

#define LED_PIN 5
#define LED_COUNT 30

const char *WIFI_SSID = "YOUR_BOAT_WIFI";
const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

Adafruit_NeoPixel strip(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
WebServer server(80);

// Presets match the dashboard's lighting.py: "solid" (any RGB color, the
// dashboard offers named color presets on top of this) and "rainbow".
String preset = "solid";
uint8_t solidR = 0, solidG = 255, solidB = 255;
float brightness = 0.6;
bool powerOn = false;
unsigned long lastFrameMs = 0;

void applyFrame() {
  for (int i = 0; i < LED_COUNT; i++) {
    uint32_t color = 0;
    if (powerOn) {
      if (preset == "rainbow") {
        uint16_t hue = (uint16_t)(millis() * 3 + (uint32_t)i * 65536UL / LED_COUNT);
        color = strip.ColorHSV(hue, 255, (uint8_t)(255 * brightness));
      } else {
        color = strip.Color(solidR * brightness, solidG * brightness, solidB * brightness);
      }
    }
    strip.setPixelColor(i, color);
  }
  strip.show();
}

void handleGetState() {
  StaticJsonDocument<256> doc;
  doc["preset"] = preset;
  doc["on"] = powerOn;
  doc["brightness"] = brightness;
  JsonArray color = doc.createNestedArray("solid_color");
  color.add(solidR); color.add(solidG); color.add(solidB);

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

void handlePostLighting() {
  if (!server.hasArg("plain")) { server.send(400, "application/json", "{\"error\":\"no body\"}"); return; }

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, server.arg("plain"));
  if (err) { server.send(400, "application/json", "{\"error\":\"bad json\"}"); return; }

  if (doc.containsKey("preset")) {
    String requested = doc["preset"].as<String>();
    if (requested != "solid" && requested != "rainbow") { server.send(400, "application/json", "{\"error\":\"unknown preset\"}"); return; }
    preset = requested;
  }
  if (doc.containsKey("on")) powerOn = doc["on"].as<bool>();
  // Clamped like the dashboard's own API: out of range, a brightness of 2 or a red of 300 wrapped
  // around when narrowed to a byte and showed some other level or colour entirely.
  if (doc.containsKey("brightness")) brightness = constrain(doc["brightness"].as<float>(), 0.0f, 1.0f);
  if (doc.containsKey("r") && doc.containsKey("g") && doc.containsKey("b")) {
    solidR = constrain(doc["r"].as<int>(), 0, 255);
    solidG = constrain(doc["g"].as<int>(), 0, 255);
    solidB = constrain(doc["b"].as<int>(), 0, 255);
    preset = "solid";
  }

  applyFrame();
  handleGetState();
}

void setup() {
  Serial.begin(115200);
  strip.begin();
  strip.show();

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.println();
  Serial.print("boat_rgb_node up at http://");
  Serial.println(WiFi.localIP());

  server.on("/api/lighting", HTTP_GET, handleGetState);
  server.on("/api/lighting", HTTP_POST, handlePostLighting);
  server.begin();
}

void loop() {
  server.handleClient();
  if (powerOn && preset == "rainbow" && millis() - lastFrameMs > 30) {
    lastFrameMs = millis();
    applyFrame();
  }
}

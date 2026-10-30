const { createRunOncePlugin, withGradleProperties } = require("expo/config-plugins");

const GRADLE_JVM_ARGS = "-Xmx4096m -XX:MaxMetaspaceSize=1024m";

function withAndroidGradleMemory(config) {
  return withGradleProperties(config, (config) => {
    const properties = config.modResults;
    const existing = properties.find(
      (property) => property.type === "property" && property.key === "org.gradle.jvmargs",
    );

    if (existing) {
      existing.value = GRADLE_JVM_ARGS;
    } else {
      properties.push({
        type: "property",
        key: "org.gradle.jvmargs",
        value: GRADLE_JVM_ARGS,
      });
    }

    return config;
  });
}

module.exports = createRunOncePlugin(
  withAndroidGradleMemory,
  "with-android-gradle-memory",
  "1.0.0",
);

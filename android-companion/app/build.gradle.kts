plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "org.oneguyos.connect.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "org.oneguyos.connect.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "0.1.0-beta1"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

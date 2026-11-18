plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

val versionMajor = 0
val versionMinor = 5
val versionPatch = 2
val versionBuild = System.getenv("BUILD_NUMBER")?.toIntOrNull() ?: 0

android {
    namespace = "bypass.whitelist"
    compileSdk {
        version = release(36)
    }

    defaultConfig {
        applicationId = "io.github.bibibobo479.telemostvpn"
        minSdk = 23
        targetSdk = 36
        versionCode = 1_000_000 * versionMajor + 1_000 * versionMinor + versionPatch + versionBuild
        versionName = "0.1.0-beta.2"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        create("beta") {
            System.getenv("TELEMOST_SIGNING_STORE")?.let { storeFile = file(it) }
            val password = System.getenv("TELEMOST_SIGNING_PASSWORD_FILE")?.let { file(it).readText().trim() }
            storePassword = password
            keyAlias = "telemost-vpn"
            keyPassword = password
        }
    }

    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("debug")
        }
        release {
            isMinifyEnabled = false
            if (System.getenv("TELEMOST_SIGNING_STORE") != null) signingConfig = signingConfigs.getByName("beta")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    packaging { jniLibs.useLegacyPackaging = true }
    kotlinOptions {
        jvmTarget = "11"
    }
}

dependencies {
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.aar"))))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.material)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.constraintlayout)
    implementation(libs.androidx.viewpager2)
    implementation(libs.androidx.recyclerview)
    implementation(libs.zxing.android.embedded)
    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
}

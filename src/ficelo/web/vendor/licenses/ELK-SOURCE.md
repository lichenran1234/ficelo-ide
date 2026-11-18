# Source code for ELK and its generated runtime

The bundled Mermaid 12.0.0 contains elkjs 0.9.3. The elkjs and Eclipse Layout Kernel (ELK) portions, and the Xtext runtime portions, are available in source form under the Eclipse Public License 2.0. The EMF Common and Ecore portions are available in source form under the Eclipse Public License 1.0. Their source code can be obtained without charge from the public locations below.

The corresponding license texts, copyright notices, and third-party acknowledgments are provided in [ELK-NOTICES.txt](ELK-NOTICES.txt). These terms apply to the identified third-party components; ficelo's first-party code remains under its own license.

## ELK and elkjs sources

- **elkjs 0.9.3**: [source and build files at commit a8304cf79fde75bc2ab1a89d28320f53f8637436](https://github.com/kieler/elkjs/tree/a8304cf79fde75bc2ab1a89d28320f53f8637436). This includes the Java-to-JavaScript integration, Java and Xtend adaptation sources, build scripts, and generated release JavaScript. The [npm source package](https://registry.npmjs.org/elkjs/-/elkjs-0.9.3.tgz) contains the distributed JavaScript.
- **Eclipse Layout Kernel 0.9.1**: [Java, Xtend, and layout-metadata sources at commit 62d5909f96fad541bc101ad52dabaece6b7eab7e](https://github.com/eclipse-elk/elk/tree/62d5909f96fad541bc101ad52dabaece6b7eab7e), the `v0.9.1` tag.
- **Included ELK change**: [commit 7ca51784e42a24201f29bc13e458728b6fc61cdc](https://github.com/eclipse-elk/elk/commit/7ca51784e42a24201f29bc13e458728b6fc61cdc), “Consider node's size when computing available space in port placement,” from [ELK pull request 955](https://github.com/eclipse-elk/elk/pull/955).

The upstream [elkjs 0.9.3 release notes](https://github.com/kieler/elkjs/releases/tag/0.9.3) identify ELK 0.9.1 and this additional ELK change as the release's basis. The release's [build configuration](https://github.com/kieler/elkjs/blob/a8304cf79fde75bc2ab1a89d28320f53f8637436/build.gradle) explains how it selects and adapts ELK source modules and compiles the runtime with GWT. The release source and dependency locations above and below are provided for source access; they are not a claim that ficelo has independently reproduced the upstream compiled bytes.

## Sources incorporated into the generated runtime

The Java source archives below are ZIP-format JAR files. Their versions are pinned by elkjs 0.9.3's build configuration, and their package namespaces appear in its generated JavaScript.

| Component | Source access | License |
| --- | --- | --- |
| GWT 2.10.0 runtime and Java emulation | [gwt-user 2.10.0 source archive](https://repo1.maven.org/maven2/org/gwtproject/gwt-user/2.10.0/gwt-user-2.10.0-sources.jar); [GWT 2.10.0 source tree](https://github.com/gwtproject/gwt/tree/2.10.0) | Apache-2.0; retain included source notices |
| Guava 31.1-jre | [Guava source archive](https://repo1.maven.org/maven2/com/google/guava/guava/31.1-jre/guava-31.1-jre-sources.jar); [guava-gwt archive containing GWT-adapted Java sources](https://repo1.maven.org/maven2/com/google/guava/guava-gwt/31.1-jre/guava-gwt-31.1-jre.jar) | Apache-2.0; retain included source notices |
| EMF for GWT Common 2.12.4 | [emf-common source archive](https://repo1.maven.org/maven2/com/genmymodel/emf/gwt/emf-common/2.12.4/emf-common-2.12.4-sources.jar) | EPL-1.0 |
| EMF for GWT Ecore 2.12.4 | [emf-ecore source archive](https://repo1.maven.org/maven2/com/genmymodel/emf/gwt/emf-ecore/2.12.4/emf-ecore-2.12.4-sources.jar) | EPL-1.0; includes Apache-1.1 code and notices |
| Xtext Xbase Runtime 2.28.0 | [org.eclipse.xtext.xbase.lib source archive](https://repo1.maven.org/maven2/org/eclipse/xtext/org.eclipse.xtext.xbase.lib/2.28.0/org.eclipse.xtext.xbase.lib-2.28.0-sources.jar) | EPL-2.0 |

The source archives include the preferred Java source form for these runtime libraries. The ELK source tree also contains the Xtend and layout-metadata inputs used to generate Java. Compiler and development-tool dependencies are described by the upstream build files; they are distinct from the runtime components listed here.

## Apache Software Foundation acknowledgment

This product includes software developed by the Apache Software Foundation (http://www.apache.org/).

This acknowledgment applies in particular to the Apache/Xerces-derived regular-expression and datatype code retained in EMF Ecore. Its full Apache Software License 1.1 notice is preserved in [ELK-NOTICES.txt](ELK-NOTICES.txt).

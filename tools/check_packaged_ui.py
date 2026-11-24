"""Open the QML from the finished package inside VESC Tool without a controller."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zlib


def ui_from_package(path):
    data = zlib.decompress(path.read_bytes()[4:])
    if not data.startswith(b"VESC Packet\0"):
        raise ValueError("Invalid package")
    pos = len(b"VESC Packet\0")
    while pos < len(data):
        end = data.index(0, pos)
        size = int.from_bytes(data[end + 1:end + 5], "big")
        if data[pos:end] == b"qmlFile":
            return data[end + 5:end + 5 + size].decode()
        pos = end + 5 + size
    raise ValueError("Package has no AppUi")


def main():
    code = 'import Vedder.vesc.vescinterface 1.0;import "qrc:/mobile";' + ui_from_package(Path(sys.argv[2]))
    harness = """import QtQuick 2.7
Item {
    anchors.fill: parent
    property var dashboard
    Component.onCompleted: dashboard = Qt.createQmlObject(SOURCE, parent, "AppUi")
    Timer {
        interval: 1200
        running: true
        onTriggered: {
            try {
                if (!dashboard || dashboard.width < 80 || dashboard.height < 80)
                    throw new Error("Missing dashboard or empty viewport")
                let active = dashboard.children
                let speedVisible = false
                while (active.length) {
                    let next = []
                    for (let i = 0; i < active.length; i++) {
                        let item = active[i]
                        if (!item || !item.visible) continue
                        if (item.text === "km/h" && item.width > 0 && item.height > 0)
                            speedVisible = true
                        if (item.children)
                            for (let j = 0; j < item.children.length; j++) next.push(item.children[j])
                    }
                    active = next
                }
                if (!speedVisible) throw new Error("Speed dial did not appear")
                const previous = "vss-settings 1\\ngeneral true true false false 5 false 0"
                dashboard.cfgImport(previous)
                if (dashboard.cfgLines.start !== "start 5")
                    throw new Error("Shared start speed did not migrate from backup")
                dashboard.cfgImport(previous + "\\nstart 0")
                if (dashboard.cfgLines.start !== "start 0")
                    throw new Error("Separate secret start speed was lost")
                console.log("PACKAGE_DIAL_VISIBLE")
            } catch (e) { console.error(e) }
            Qt.quit()
        }
    }
}""".replace("SOURCE", json.dumps(code))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "Probe.qml"
        path.write_text(harness)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software")
        try:
            run = subprocess.run([sys.argv[1], "--loadQml", str(path), "--qmlWindowSize", "380:760"],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=18)
            output = run.stdout
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or b""
        if b"PACKAGE_DIAL_VISIBLE" not in output:
            sys.exit(output.decode(errors="replace")[-6000:] or "No dashboard output from VESC Tool")
        print("Packaged speed dial visible in VESC Tool without a controller")


if __name__ == "__main__":
    main()

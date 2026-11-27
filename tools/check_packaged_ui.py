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
    property var probe
    Timer {
        id: checkExpiry
        interval: 1250
        onTriggered: {
            if (probe && !probe.visible) console.log("PACKAGE_DIAL_VISIBLE")
            else console.error("Motor indicator did not clear")
            Qt.quit()
        }
    }
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
                if (dashboard.packTaps() !== 0) throw new Error("Tap shortcut default")
                dashboard.cfgImport(previous + "\\nstart 0\\ntaps 1290")
                if (dashboard.tapCount !== 4 || dashboard.tapSteps.join(",") !== "1,0,2,1"
                        || dashboard.packTaps() !== 1290
                        || dashboard.cfgExport().indexOf("taps 1290") < 0)
                    throw new Error("Ordered tap backup round trip")
                dashboard.loadTaps(9)
                if (dashboard.tapCount !== 2 || dashboard.packTaps() !== 9)
                    throw new Error("Two brake taps")
                dashboard.loadTaps(3)
                if (dashboard.packTaps() !== 0) throw new Error("Invalid sequence")
                function findField(item, name) {
                    if (item.objectName === name) return item
                    const children = item.children || []
                    for (let i = 0; i < children.length; i++) {
                        const found = findField(children[i], name)
                        if (found) return found
                    }
                    return null
                }
                const presses = findField(dashboard, "drivePresses")
                const brake = findField(dashboard, "driveBrake")
                const throttle = findField(dashboard, "driveThrottle")
                const locked = findField(dashboard, "driveLocked")
                if (!presses || presses.currentText !== "Off") throw new Error("Motor binding default")
                dashboard.cfgImport(previous + "\\ncommutation true")
                if (presses.currentText !== "4" || brake.checked || throttle.checked || locked.checked)
                    throw new Error("Old four-tap shortcut migration")
                dashboard.cfgImport(previous + "\\ncommutation true 0 1 true")
                if (presses.currentText !== "Hold" || !brake.checked || throttle.checked || !locked.checked
                        || dashboard.cfgExport().indexOf("commutation true 0 1 true") < 0)
                    throw new Error("Motor binding backup round trip")
                dashboard.applySettingsLine("commutation false 4 3 false")
                if (presses.currentText !== "Off") throw new Error("Motor binding disable")
                probe = findField(dashboard, "driveCode")
                dashboard.showDriveMessage("drive BLDC")
                if (!probe.visible || probe.text !== "DC" || probe.color.r <= probe.color.g)
                    throw new Error("Red BLDC indicator")
                dashboard.showDriveMessage("drive FOC")
                if (!probe.visible || probe.text !== "FC") throw new Error("FOC indicator")
                const field = findField(dashboard, "secretEntrySpeed")
                dashboard.cfgImport(previous + "\\nentry 3.0")
                if (!field || field.text !== "3" || dashboard.cfgExport().indexOf("entry 3.0") < 0)
                    throw new Error("Secret entry speed field or backup round trip")
                dashboard.cfgImport(previous)
                if (field.text !== "3") throw new Error("Older backup replaced the entry speed")
                dashboard.applySettingsLine("misc false 10 true false false 0 1 0 1 0 0 false")
                dashboard.applySettingsLine("entry 3.0")
                if (field.text !== "1.86" || Number(dashboard.readSpeed(field, 1)) !== 3)
                    throw new Error("Secret entry speed in mph")
                checkExpiry.start()
            } catch (e) { console.error(e); Qt.quit() }
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

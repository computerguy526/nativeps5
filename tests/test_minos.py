#!/usr/bin/env python3
"""scripts/check-minos.sh: a Mach-O built for macOS 27 fails the 26.0 gate, one built for 26.0 passes; build-kosmickrisp.sh and
build.sh default the deployment target to 26.0. Builds two one-line dylibs with the host clang (macOS only)."""
import os, re, shutil, subprocess, sys, tempfile, unittest
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "check-minos.sh")
CLANG = shutil.which("clang")


def read(*parts):
    with open(os.path.join(HERE, "..", *parts)) as f:
        return f.read()


class Minos(unittest.TestCase):
    def test_build_scripts_default_to_the_oldest_supported_macos(self):
        for rel in (("scripts", "build.sh"), ("kosmickrisp", "build-kosmickrisp.sh")):
            text = read(*rel)
            self.assertRegex(text, r'export MACOSX_DEPLOYMENT_TARGET="\$\{MACOSX_DEPLOYMENT_TARGET:-26\.0\}"', rel)
        self.assertIn("check-minos.sh", read("kosmickrisp", "build-kosmickrisp.sh"))

    @unittest.skipUnless(sys.platform == "darwin" and CLANG, "needs macOS clang")
    def test_gate_accepts_26_and_refuses_27(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "x.c")
            open(src, "w").write("int f(void){return 1;}\n")
            for ver, ok in (("26.0", True), ("27.0", False)):
                out = os.path.join(d, "x%s.dylib" % ver)
                subprocess.run([CLANG, "-shared", "-arch", "arm64", "-mmacosx-version-min=" + ver, src, "-o", out], check=True,
                               capture_output=True)
                r = subprocess.run(["bash", SCRIPT, "--max", "26.0", out], capture_output=True, text=True)
                self.assertEqual(r.returncode == 0, ok, (ver, r.stdout, r.stderr))
            r = subprocess.run(["bash", SCRIPT, "--max", "26.0", os.path.join(d, "x26.0.dylib"), os.path.join(d, "x27.0.dylib")],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)  # one bad file fails the whole call


if __name__ == "__main__":
    unittest.main()

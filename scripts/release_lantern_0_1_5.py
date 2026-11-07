from pathlib import Path
import hashlib, gzip, email.utils, os, re, subprocess, tempfile, json

PKG = "com.ochium.lantern"
OLD, NEW = "0.1.4-1", "0.1.5-1"
OLD_DEB = Path(f"debs/{PKG}_{OLD}_iphoneos-arm64.deb")
NEW_DEB = Path(f"debs/{PKG}_{NEW}_iphoneos-arm64.deb")
OLD_SHA256 = "e0405f41558921263dca44ae1a940707ff9455e338161a40de03933e3b9f887d"
DEPENDS = "mobilesubstrate, com.opa334.ccsupport, cy+model.iphone (<< 14.7)"
CHANGELOG = "Sileo now requests a device restart after installation or update to activate the warm flashlight. You can choose Reboot Device Later. No changes to the flashlight code or supported hardware."
POSTINST = """#!/bin/sh
# Request Sileo's deferred restart action; do not reboot from dpkg.
# Sileo implements finish:reboot using ldrestart on current iOS.
if [ "${1:-}" = "configure" ] && [ -n "${CYDIA:-}" ]; then
    set -- $CYDIA
    case "${1:-}" in ''|*[!0-9]*) exit 0 ;; esac
    case "${2:-}" in ''|*[!0-9]*) exit 0 ;; esac
    if [ "$2" -ge 1 ]; then
        eval "printf 'finish:reboot\\n' >&$1" || :
    fi
fi
exit 0
"""

def replace_once(text, old, new):
    n=text.count(old)
    if n != 1:
        raise RuntimeError(f"Expected one {old[:80]!r}, found {n}")
    return text.replace(old, new, 1)

def sha(data, kind): return hashlib.new(kind, data).hexdigest()

def field(deb, name):
    return subprocess.check_output(["dpkg-deb", "-f", str(deb), name], text=True).strip()

def manifest(directory):
    output={}
    for path in sorted(directory.rglob("*")):
        rel=path.relative_to(directory)
        if rel.parts[0]=="DEBIAN": continue
        name=str(rel)
        if path.is_symlink(): output[name]=("link",os.readlink(path))
        elif path.is_file(): output[name]=("file",sha(path.read_bytes(),"sha256"),path.stat().st_mode&0o7777)
        elif path.is_dir(): output[name]=("dir",path.stat().st_mode&0o7777)
        else: raise RuntimeError(f"Unexpected payload file type: {path}")
    return output

assert OLD_DEB.exists() and not NEW_DEB.exists()
assert sha(OLD_DEB.read_bytes(),"sha256")==OLD_SHA256
assert field(OLD_DEB,"Package")==PKG
assert field(OLD_DEB,"Version")==OLD
assert field(OLD_DEB,"Depends")==DEPENDS

with tempfile.TemporaryDirectory() as temp:
    before=Path(temp)/"before"
    after=Path(temp)/"after"
    subprocess.run(["dpkg-deb","-R",str(OLD_DEB),str(before)],check=True)
    initial=manifest(before)
    old_ctrl=(before/"DEBIAN/control").read_text()
    new_ctrl=replace_once(old_ctrl,f"Version: {OLD}\n",f"Version: {NEW}\n")
    (before/"DEBIAN/control").write_text(new_ctrl)
    postinst=(before/"DEBIAN/postinst")
    assert not postinst.exists(), "Previous package unexpectedly has postinst"
    postinst.write_text(POSTINST)
    postinst.chmod(0o755)
    # A real named fd, matching the Sileo CYDIA=6 1 protocol, must receive
    # the request; normal shell dpkg invocation must not request or execute reboot.
    out=Path(temp)/"finish-request.txt"
    command=f'CYDIA="3 1" /bin/sh "{postinst}" configure 3>"{out}"'
    subprocess.run(["/bin/sh","-c",command],check=True)
    assert out.read_text()=="finish:reboot\n",f"Invalid finish request: {out.read_text()!r}"
    subprocess.run(["/bin/sh",str(postinst),"configure"],check=True)
    subprocess.run(["dpkg-deb","--build","--root-owner-group",str(before),str(NEW_DEB)],check=True)
    subprocess.run(["dpkg-deb","-R",str(NEW_DEB),str(after)],check=True)
    assert initial==manifest(after),"Installed package payload changed"
    assert (after/"DEBIAN/postinst").read_text()==POSTINST
    assert (after/"DEBIAN/postinst").stat().st_mode&0o111,"postinst not executable"
    assert field(NEW_DEB,"Package")==PKG and field(NEW_DEB,"Version")==NEW
    assert field(NEW_DEB,"Depends")==DEPENDS

index_path=Path("Packages")
index=index_path.read_text()
stanzas=index.split("\n\n")
matches=[s for s in stanzas if s.startswith(f"Package: {PKG}\n") and f"Version: {OLD}\n" in s+"\n"]
assert len(matches)==1
old_stanza=matches[0]
new_stanza=replace_once(old_stanza, f"Version: {OLD}\n", f"Version: {NEW}\n")
new_stanza=replace_once(new_stanza, f"Filename: debs/{PKG}_{OLD}_iphoneos-arm64.deb\n",
                        f"Filename: debs/{PKG}_{NEW}_iphoneos-arm64.deb\n")
data=NEW_DEB.read_bytes()
for name, content in [("Size",str(len(data))),("MD5sum",sha(data,"md5")),
                      ("SHA1",sha(data,"sha1")),("SHA256",sha(data,"sha256")),
                      ("SHA512",sha(data,"sha512"))]:
    new_stanza,n=re.subn(rf"(?m)^{name}: [^\n]+$",f"{name}: {content}",new_stanza)
    assert n==1,name
index=replace_once(index,old_stanza,new_stanza+"\n\n"+old_stanza)
index_path.write_text(index)
compressed=gzip.compress(index.encode(),compresslevel=9,mtime=0)
Path("Packages.gz").write_bytes(compressed)

release_path=Path("Release")
release=release_path.read_text()
head=release.split("MD5Sum:\n")[0]
assert len(release.split("MD5Sum:\n"))==2
head,n=re.subn(r"(?m)^Date: .*$",f"Date: {email.utils.formatdate(usegmt=True)}",head)
assert n==1
sections=[]
for name,algorithm in [("MD5Sum","md5"),("SHA1","sha1"),("SHA256","sha256"),("SHA512","sha512")]:
    lines=[name+":"]
    for path,body in [("Packages",index.encode()),("Packages.gz",compressed)]:
        lines.append(f" {sha(body,algorithm)} {len(body)} {path}")
    sections.append("\n".join(lines)+"\n")
release_path.write_text(head+"".join(sections))

html_path=Path("depiction.html")
html=html_path.read_text()
html=replace_once(html,f"Version {OLD} · Free",f"Version {NEW} · Free")
html=replace_once(html,f"What's new in {OLD}",f"What's new in {NEW}")
html=replace_once(html,
    "Installation is now restricted to iPhones with warm/white flashlight LED hardware (iPhone 13 series and earlier, including SE 3). No changes to flashlight functionality.",
    CHANGELOG)
html=replace_once(html,f"Version {OLD} preserves those installed binaries and resources.",
                  f"Version {NEW} preserves those installed binaries and resources.")
html=replace_once(html,
    "The package has no automatic activation scripts. Restart cameracaptured and reload SpringBoard using the",
    "Sileo requests Reboot Device or Reboot Device Later after installation. Finish the restart to activate Lantern. If you installed from a terminal instead, use the")
html_path.write_text(html)

json_path=Path("depictions/lantern.json")
description=json_path.read_text()
description=replace_once(description,f'"title": "Version {OLD}"',f'"title": "Version {NEW}"')
description=replace_once(description,f'"text": "{OLD}"',f'"text": "{NEW}"')
description=replace_once(description,
    "Installation is now restricted to iPhones with warm/white flashlight LED hardware (iPhone 13 series and earlier, including SE 3). No changes to flashlight functionality.",
    CHANGELOG)
description=replace_once(description, f"Version {OLD} preserves identical installed binaries/resources.",
                         f"Version {NEW} preserves identical installed binaries/resources.")
json.loads(description)
json_path.write_text(description)

home_path=Path("index.html")
home=home_path.read_text()
home=replace_once(home,f'<span class="tag">{OLD}</span>',f'<span class="tag">{NEW}</span>')
home_path.write_text(home)

assert gzip.decompress(compressed).decode()==index
assert index.count(f"Version: {NEW}\n")==1
assert "cy+model.iphone (<< 14.7)" in new_stanza
assert field(NEW_DEB,"Version")==NEW
print(f"Verified Lantern {NEW}: {sha(data,'sha256')} size={len(data)}; finish:reboot deferred; payload unchanged")

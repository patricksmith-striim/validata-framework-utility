"""Both engines rebuild an OP jar when anything Maven compiles into it changed: one input set.

The input set is the module's src/main and pom.xml, every root its pom add-sources (a shared
library such as OpenProcessorCommon or FieldCommon), and the parent poms it inherits from.
"""
import importlib
import os
import types
import zipfile
from pathlib import Path

import pytest

ENGINES = ['livetest', 'inttest']
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'

POM = """<project>
  <!-- <parent><relativePath>../../nowhere/pom.xml</relativePath></parent> -->
  <parent><groupId>g</groupId><artifactId>op-parent</artifactId><version>1</version></parent>
  <build><plugins><plugin><executions><execution><configuration><sources>
    <source>${project.basedir}/../OpenProcessorCommon/src/main/java</source>
    <source>${project.basedir}/../../FieldCommon/src/main/java</source>
  </sources></configuration></execution></executions></plugin></plugins></build>
</project>
"""
OLD, JAR_TIME, EDIT_TIME = 1_700_000_000, 1_700_000_100, 1_700_000_500


def _engine(name):
    return importlib.import_module(name + '.opartifacts')


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'proj'
    mod = root / 'java/OpenProcessors/MyOp'
    files = {
        'own': mod / 'src/main/java/m/MyOp.java',
        'common': root / 'java/OpenProcessors/OpenProcessorCommon/src/main/java/c/Common.java',
        'field': root / 'java/FieldCommon/src/main/java/f/Shared.java',
        'field2': root / 'java/FieldCommon/src/main/java/f/Other.java',
        'parent': root / 'java/OpenProcessors/pom.xml',
        'aggregator': root / 'java/pom.xml',
    }
    for f in files.values():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('// v1\n')
    files['parent'].write_text('<project><modules><module>MyOp</module></modules></project>')
    (mod / 'pom.xml').write_text(POM)
    jar = mod / 'target/MyOp-5.4.jar'
    jar.parent.mkdir()
    with zipfile.ZipFile(jar, 'w') as z:
        z.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\n')
    for name in ENGINES:
        monkeypatch.setattr(_engine(name), '_root', lambda: root)
    for key in list(os.environ):
        if key.startswith(('SLT_PREBUILT_', 'STRIIM_BUILD_', 'FTC_')) or key == 'STRIIM_HOME':
            monkeypatch.delenv(key)
    for f in [*files.values(), mod / 'pom.xml']:
        os.utime(f, (OLD, OLD))
    return types.SimpleNamespace(root=root, mod=mod, jar=jar, **files)


def _built(engine, p):
    """The state build_jar leaves: the jar and its source record, newer than every source."""
    engine._write_source_record(p.jar, p.mod)
    os.utime(p.jar, (JAR_TIME, JAR_TIME))
    assert engine._stale_trigger(p.jar, p.mod) is None


def _edit(path):
    path.write_text('// v2: behaviour changed\n')
    os.utime(path, (EDIT_TIME, EDIT_TIME))


def _same(a, b):
    return a is not None and Path(os.path.normpath(a)).resolve() == b.resolve()


@pytest.mark.parametrize('name', ENGINES)
@pytest.mark.parametrize('edited', ['own', 'common', 'field', 'parent'])
def test_an_edit_to_any_input_is_a_rebuild_trigger(project, name, edited):
    engine = _engine(name)
    _built(engine, project)
    _edit(getattr(project, edited))
    assert _same(engine._stale_trigger(project.jar, project.mod), getattr(project, edited))


@pytest.mark.parametrize('edited', ['own', 'common', 'field', 'parent'])
def test_both_engines_name_the_same_trigger(project, edited):
    live, integration = _engine('livetest'), _engine('inttest')
    _built(integration, project)
    _edit(getattr(project, edited))
    assert _same(live._stale_trigger(project.jar, project.mod), getattr(project, edited))
    assert _same(integration._stale_trigger(project.jar, project.mod), getattr(project, edited))


@pytest.mark.parametrize('name', ENGINES)
@pytest.mark.parametrize('deleted', ['own', 'field'])
def test_a_deleted_input_is_a_rebuild_trigger(project, name, deleted):
    engine = _engine(name)
    _built(engine, project)
    getattr(project, deleted).unlink()
    trigger = engine._stale_trigger(project.jar, project.mod)
    assert _same(trigger, getattr(project, deleted)) and not trigger.exists()


@pytest.mark.parametrize('name', ENGINES)
def test_an_aggregator_pom_the_module_does_not_inherit_from_is_not_an_input(project, name):
    engine = _engine(name)
    _built(engine, project)
    _edit(project.aggregator)        # java/pom.xml lists modules; nothing declares it a parent
    assert engine._stale_trigger(project.jar, project.mod) is None


@pytest.mark.parametrize('name', ENGINES)
def test_a_jar_without_a_source_record_is_a_rebuild_trigger(project, name):
    engine = _engine(name)
    os.utime(project.jar, (JAR_TIME, JAR_TIME))
    assert engine._stale_trigger(project.jar, project.mod) == engine._source_record(project.jar)


@pytest.mark.parametrize('pom,expected', [
    ('<project/>', []),                                                         # no parent
    ('<project><parent><artifactId>p</artifactId></parent></project>', ['java/OpenProcessors/pom.xml']),
    ('<project><parent><relativePath/></parent></project>', []),                # repository only
    ('<project><parent><relativePath></relativePath></parent></project>', []),
    ('<project><parent><relativePath>../..</relativePath></parent></project>', ['java/pom.xml']),
    ('<project><parent><relativePath>../../pom.xml</relativePath></parent></project>', ['java/pom.xml']),
    ('<project><parent><relativePath>../missing.xml</relativePath></parent></project>', []),
])
def test_parent_poms_follow_maven_relative_path(project, pom, expected):
    (project.mod / 'pom.xml').write_text(pom)
    for name in ENGINES:
        jarinputs = importlib.import_module(name + '.jarinputs')
        assert jarinputs.parent_poms(project.mod) == [(project.root / e).resolve() for e in expected]


def test_parent_poms_follow_the_chain_and_stop_on_a_cycle(project):
    project.parent.write_text('<project><parent><relativePath>../pom.xml</relativePath></parent></project>')
    project.aggregator.write_text('<project><parent><relativePath>OpenProcessors</relativePath></parent></project>')
    from inttest import jarinputs
    assert jarinputs.parent_poms(project.mod) == [project.parent.resolve(), project.aggregator.resolve()]


def test_integration_build_jar_rebuilds_after_a_fieldcommon_edit(project, monkeypatch):
    engine = _engine('inttest')
    monkeypatch.setattr(engine.opjarrelease, 'configured_versions', lambda *a, **k: set())
    monkeypatch.setattr(engine, 'resolve_build_java_home', lambda *a, **k: None)
    monkeypatch.setattr(engine, '_verify_striim_home', lambda *a, **k: None)
    monkeypatch.setattr(engine, '_assert_jar_java_version', lambda *a, **k: None)
    _built(engine, project)
    _edit(project.field)
    ran = []

    def mvn(argv, cwd, env):
        ran.append(argv)
        os.utime(project.jar, (EDIT_TIME + 1, EDIT_TIME + 1))
        return types.SimpleNamespace(returncode=0, stdout='', stderr='')

    release = {'STRIIM_SERIES': '5.4', 'STRIIM_VERSION': '5.4.2', 'JAVA_RELEASE': '17'}
    built = engine.build_jar('java/OpenProcessors/MyOp', release, run=mvn)
    assert built.origin == 'built' and ran and ran[0][0] == 'mvn'


def test_live_build_jar_clean_rebuilds_after_a_source_is_deleted(project, monkeypatch):
    engine = _engine('livetest')
    monkeypatch.setattr(engine.opjarrelease, 'configured_versions', lambda *a, **k: set())
    monkeypatch.setattr(engine, 'resolve_build_java_home', lambda *a, **k: None)
    monkeypatch.setattr(engine, '_verify_striim_home', lambda *a, **k: None)
    monkeypatch.setattr(engine, '_require_platform_home', lambda *a, **k: None)
    monkeypatch.setattr(engine, '_assert_jar_java_version', lambda *a, **k: None)
    _built(engine, project)
    project.field2.unlink()
    ran, reasons = [], []

    def mvn(argv, cwd, env):
        ran.append(argv)
        os.utime(project.jar, (EDIT_TIME + 1, EDIT_TIME + 1))
        return types.SimpleNamespace(returncode=0, stdout='', stderr='')

    release = {'STRIIM_SERIES': '5.4', 'STRIIM_VERSION': '5.4.2', 'JAVA_RELEASE': '17'}
    built = engine.build_jar('java/OpenProcessors/MyOp', release, run=mvn, report=reasons.append)
    assert built.origin == 'built' and 'clean' in ran[0]
    assert 'source removed' in reasons[0]
    assert engine._stale_trigger(project.jar, project.mod) is None     # the new record holds


def test_input_set_helper_is_identical_between_engines():
    assert (SCRIPTS / 'live/livetest/jarinputs.py').read_bytes() == (
        SCRIPTS / 'integration/inttest/jarinputs.py').read_bytes()

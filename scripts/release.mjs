import {spawnSync} from 'node:child_process';
import {existsSync, readFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const rootDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const frontendDir = resolve(rootDir, 'frontend');
const backendDir = resolve(rootDir, 'backend');

const packages = {
    python: {
        displayName: 'orcestr-commerce-solana',
        kind: 'python',
        manifest: resolve(backendDir, 'pyproject.toml'),
        tagPrefix: 'python-v',
    },
    core: npmPackage(
        'core',
        '@orcestr/commerce-solana-core',
        'commerce-solana-core-v',
    ),
    react: npmPackage(
        'react',
        '@orcestr/commerce-solana-react',
        'commerce-solana-react-v',
    ),
    ui: npmPackage(
        'ui',
        '@orcestr/commerce-solana-ui',
        'commerce-solana-ui-v',
    ),
};

class ReleaseError extends Error {}

function npmPackage(directory, displayName, tagPrefix) {
    return {
        displayName,
        kind: 'npm',
        manifest: resolve(frontendDir, 'packages', directory, 'package.json'),
        tagPrefix,
    };
}

function runCommand(command, args, options = {}) {
    const result = spawnSync(command, args, {
        cwd: options.cwd ?? rootDir,
        encoding: 'utf8',
        shell: options.shell ?? false,
        stdio: options.captureOutput ? 'pipe' : 'inherit',
    });

    if (result.error) {
        throw new ReleaseError(result.error.message);
    }

    if (result.status !== 0 && options.check !== false) {
        throw new ReleaseError(`${command} ${args.join(' ')} failed`);
    }

    return result;
}

function git(args, options = {}) {
    return runCommand('git', args, options);
}

function npm(args, options = {}) {
    const npmCliPath = findNpmCli();

    if (npmCliPath) {
        return runCommand(process.execPath, [npmCliPath, ...args], options);
    }

    return runCommand(process.platform === 'win32' ? 'npm.cmd' : 'npm', args, {
        ...options,
        shell: process.platform === 'win32',
    });
}

function findNpmCli() {
    const candidates = [
        process.env.npm_execpath,
        process.platform === 'win32'
            ? resolve(dirname(process.execPath), 'node_modules/npm/bin/npm-cli.js')
            : resolve(dirname(process.execPath), '../lib/node_modules/npm/bin/npm-cli.js'),
    ].filter(Boolean);

    return candidates.find((path) => existsSync(path));
}

function uv(args, options = {}) {
    return runCommand(process.platform === 'win32' ? 'uv.exe' : 'uv', args, options);
}

function readVersion(config) {
    if (config.kind === 'npm') {
        return JSON.parse(readFileSync(config.manifest, 'utf8')).version;
    }

    const pyproject = readFileSync(config.manifest, 'utf8');
    const projectSection = pyproject.match(/\[project\]\s+([\s\S]*?)(?=\n\[|$)/u)?.[1];
    const version = projectSection?.match(/^version\s*=\s*"([^"]+)"/mu)?.[1];

    if (!version) {
        throw new ReleaseError('Could not read [project].version from backend/pyproject.toml.');
    }

    return version;
}

function bumpVersion(version, part) {
    const match = /^(\d+)\.(\d+)\.(\d+)$/u.exec(version);

    if (!match) {
        throw new ReleaseError(`Unsupported stable package version: ${version}`);
    }

    const [, majorText, minorText, patchText] = match;
    const major = Number.parseInt(majorText, 10);
    const minor = Number.parseInt(minorText, 10);
    const patch = Number.parseInt(patchText, 10);

    if (part === 'patch') {
        return `${major}.${minor}.${patch + 1}`;
    }

    if (part === 'minor') {
        return `${major}.${minor + 1}.0`;
    }

    if (part === 'major') {
        return `${major + 1}.0.0`;
    }

    throw new ReleaseError(`Unsupported release part: ${part}`);
}

function checkWorktreeIsClean() {
    const result = git(['status', '--short'], {captureOutput: true});

    if (result.stdout.trim()) {
        throw new ReleaseError('Git worktree is not clean. Commit changes before release.');
    }
}

function checkPreparationBranch() {
    const result = git(['branch', '--show-current'], {captureOutput: true});
    const branch = result.stdout.trim();
    if (!branch || branch === 'main' || branch === 'master') {
        throw new ReleaseError(
            'Prepare a release on a feature/fix branch, then merge it through a pull request.',
        );
    }
}

function writeVersion(config, part) {
    if (config.kind === 'python') {
        uv(['version', '--bump', part, '--no-sync'], {cwd: backendDir});
        return;
    }

    npm(
        [
            'version',
            part,
            '--workspace',
            config.displayName,
            '--no-git-tag-version',
            '--ignore-scripts',
        ],
        {cwd: frontendDir},
    );
}

function prepare(target, part, options) {
    const config = packages[target];

    if (!config) {
        throw new ReleaseError(`Unsupported package: ${target}`);
    }

    const currentVersion = readVersion(config);
    const nextVersion = bumpVersion(currentVersion, part);
    const tagName = `${config.tagPrefix}${nextVersion}`;

    console.log(`Package: ${config.displayName}`);
    console.log(`Current version: ${currentVersion}`);
    console.log(`Next version: ${nextVersion}`);
    console.log(`Tag: ${tagName}`);

    if (options.dryRun) {
        console.log('Dry run mode. No files or git objects were changed.');
        return;
    }

    checkWorktreeIsClean();
    checkPreparationBranch();
    writeVersion(config, part);

    const writtenVersion = readVersion(config);
    if (writtenVersion !== nextVersion) {
        throw new ReleaseError(
            `Version command wrote ${writtenVersion}, expected ${nextVersion}.`,
        );
    }

    console.log('Release manifests were prepared without committing or tagging.');
    console.log('Review and test the diff, commit it on this branch, and merge it through a pull request.');
    console.log(`After merge, create ${tagName} on the exact merged commit and push that tag.`);
}

function main() {
    const [, , target, part, ...flags] = process.argv;
    const allowedTargets = new Set(Object.keys(packages));
    const allowedParts = new Set(['patch', 'minor', 'major']);

    if (!allowedTargets.has(target) || !allowedParts.has(part)) {
        throw new ReleaseError(
            'Usage: node scripts/release.mjs <python|core|react|ui> '
                + '<patch|minor|major> [--dry-run]',
        );
    }

    if (flags.some((flag) => flag !== '--dry-run')) {
        throw new ReleaseError(`Unsupported flag: ${flags.join(' ')}`);
    }

    prepare(target, part, {
        dryRun: flags.includes('--dry-run'),
    });
}

try {
    main();
} catch (error) {
    console.error(`Release error: ${error.message}`);
    process.exit(1);
}

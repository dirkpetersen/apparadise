# User identity in containers

Every apparadise container must run **as the user who started it, with that user's own UID,
GID and AD groups**. Then files written to CephFS belong to the user, and group-owned
project directories work exactly as they do outside the container (Q36, owner, 2026-11-01).

## The key point, in plain terms

There are two ways a container can deal with users.

1. **"Root inside, mapped outside" (user namespaces).** Inside the container `id` says
   `root`, and the kernel translates that to some other UID whenever the process touches
   the host.
2. **"The same user inside and outside" (run as UID).** The process really is UID 12345 in
   the container and on the host. Nothing is translated, and nothing ever runs as root.

The first way works for your *own* UID only on your own machine: rootless podman, or
Enroot with `--container-remap-root`, map root inside to *you* outside.

**Kubernetes, and therefore Run:ai, cannot do that.** Kubernetes does have user namespaces
(`hostUsers: false`, stable since 1.36). But:

- The host UID is a range **the kubelet picks per pod, above 65535**, so never the user's AD
  UID. The docs say "the kubelet will pick host UIDs/GIDs a pod is mapped to" and "you cannot
  use any ID from the range 0-65535 for Pods".
- On volumes, the mapping does not count. "the inodes created/read in volumes mounted by the
  pod will be the same as if the pod wasn't using user namespaces", which are owned by the
  UID **inside** the container. So root inside writes files owned by root on shared storage.
- "NFS volumes cannot be mounted in a user-namespace pod". The volume filesystems must
  support idmap mounts.

Run:ai 3.0 offers no user-namespace setting at all. Its security fields are `uidGidSource`
(`fromTheImage`, `fromIdpToken`, custom), `runAsUid`, `runAsGid`, `supplementalGroups`,
`runAsNonRoot`, `allowPrivilegeEscalation`, `privileged`, `capabilities`,
`seccompProfileType`, `readOnlyRootFilesystem` and `createHomeDir`. Its user-identity page
warns that "if the container's root identity is propagated to external systems (e.g.,
network-attached storage), it can result in elevated permissions outside the container".

**So: to run a container as a given user on Kubernetes or Run:ai, it must be that user both
inside and outside.** The image has to work when it is started as an ordinary, unknown
user. That is the whole reason for the image rules below.

On Slurm, Pyxis/Enroot already does this. It runs the container as the submitting user with
their groups, adds them to the container's `/etc/passwd`, mounts their home, and ignores the
image's `USER` and (by default) its entrypoint.

## What every apparadise image does

apparadise builds all its images itself, so it meets these rules once, in the builder
(`builder/apparadise_builder/agent.py`):

| Rule | How |
|---|---|
| Installed software is readable by everyone | `chmod -R a+rX` on the install tree in the build stage, so the final image does not carry a second copy of every file |
| No assumed user name, no `passwd` entry needed | nothing in the image looks up a user. Enroot adds one, Run:ai `createHomeDir` gives a home |
| Nothing is written into the image at runtime | software lives in `/opt` and `/work`; writes go to `$HOME`, `/tmp` or mounted storage |
| No root at start-up | no `ENTRYPOINT`, no setuid binaries; works with Pyxis's "no entrypoint" default |
| Numeric non-root default user | `USER 65534:65534` (nobody): `runAsNonRoot` accepts it, and anything that starts the image without a UID gets no root |
| Checked on every build | the `package` stage runs the image as uid `54321:54321` and fails if `id -u` is wrong or anything under `/opt` or `/work` is unreadable (no GPU needed) |

Third-party images (NGC, Docker Hub) are not changed. Many work as any UID, some do not.
Pyxis runs them all as the user anyway. For Run:ai, test one first with
`podman run --user 12345:12345 <image> <command>`.

## Run:ai settings for the cluster administrators

From the Run:ai 3.0 docs ("User Identity in Containers", "Security Best Practices", "Policy
YAML reference"):

1. **SSO with UID/GID from AD.** Set up single sign-on and the "UID/GID integration" so the
   IdP token carries each user's UID, GID and groups. Store them in AD (`uidNumber`,
   `gidNumber`, group membership).
2. **Policy:** set the security section to take identity from the token and lock it, so
   users cannot switch back to root.
   ```yaml
   defaults:
     security:
       uidGidSource: fromIdpToken
       runAsNonRoot: true
       allowPrivilegeEscalation: false
       createHomeDir: true
   rules:
     security:
       uidGidSource: {canEdit: false}
       runAsNonRoot: {canEdit: false}
       allowPrivilegeEscalation: {canEdit: false}
   ```
   Check the exact field and rule syntax against the policy reference for your Run:ai
   version; the structure above follows its examples.
3. **Supplementary groups** must come through (from the token, or the `supplementalGroups`
   field). Without them a user cannot reach group-owned project directories on CephFS.
4. **`createHomeDir`** (CLI `--create-home-dir`; on by default with `--run-as-user`) gives
   the unknown user a temporary `/home/<username>`. Its contents are lost when the container
   ends, so real data goes on mounted storage.
5. **OpenShift only:** edit the `runai-user-job` (and `runai-jupyter-notebook`) SCCs to
   `runAsUser: RunAsAny` and `supplementalGroups: RunAsAny`. Otherwise OpenShift assigns a
   random UID.
6. On plain Kubernetes, Gatekeeper (or Pod Security admission `restricted`) can also
   refuse root pods cluster-wide.

## Sources

Downloaded and read on 2026-11-01:

- Kubernetes, [User Namespaces](https://kubernetes.io/docs/concepts/workloads/pods/user-namespaces/)
  (feature gate `UserNamespacesSupport`: beta on by default 1.33–1.35, stable from 1.36) and
  [Configure a Security Context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/).
- NVIDIA Run:ai self-hosted v3.0:
  [User Identity in Containers](https://docs.nvidia.com/run-ai/self-hosted/documentation/installation-and-configuration/config/security/user-identity-in-containers),
  [Security Best Practices](https://docs.nvidia.com/run-ai/self-hosted/documentation/installation-and-configuration/config/security/security-best-practices),
  [Policy YAML reference](https://docs.nvidia.com/run-ai/self-hosted/v3.0/documentation/platform-management/policies/native-workload-policies/policy-yaml-reference).

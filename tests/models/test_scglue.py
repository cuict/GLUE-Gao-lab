r"""
Tests for the :mod:`scglue.models.scglue` module
"""

# pylint: disable=redefined-outer-name, wildcard-import, unused-wildcard-import

import warnings

import pytest

import scglue

from ..fixtures import *
from ..utils import cmp_arrays


@pytest.mark.parametrize("use_uid", ["uid", None])
def test_anndataset(rna, atac, use_uid):
    scglue.models.configure_dataset(rna, "NB", use_highly_variable=False, use_layer="arange", use_uid=use_uid)
    scglue.models.configure_dataset(atac, "NB", use_highly_variable=False, use_layer="arange", use_uid=use_uid)
    with pytest.raises(ValueError):
        dataset = scglue.models.scglue.AnnDataset(
            [rna[[], :], atac],
            [rna.uns[scglue.config.ANNDATA_KEY], atac.uns[scglue.config.ANNDATA_KEY]],
            mode="train", getitem_size=5
        )
    with pytest.raises(ValueError):
        dataset = scglue.models.scglue.AnnDataset(
            [rna, atac],
            [rna.uns[scglue.config.ANNDATA_KEY]],
            mode="train", getitem_size=5
        )
    with pytest.raises(ValueError):
        dataset = scglue.models.scglue.AnnDataset(
            [rna, atac],
            [rna.uns[scglue.config.ANNDATA_KEY], atac.uns[scglue.config.ANNDATA_KEY]],
            mode="xxx", getitem_size=5
        )
    dataset = scglue.models.scglue.AnnDataset(
        [rna, atac],
        [rna.uns[scglue.config.ANNDATA_KEY], atac.uns[scglue.config.ANNDATA_KEY]],
        getitem_size=5
    )
    with pytest.raises(ValueError):
        dataset.random_split([-0.2, 1.2])
    with pytest.raises(ValueError):
        dataset.random_split([0.2, 0.3])
    dataset.prepare_shuffle()
    dataset.shuffle()
    for i in range(len(dataset)):
        x1, x2, *_, pmsk = dataset[i]
        x1, x2, pmsk = x1.numpy(), x2.numpy(), pmsk.numpy()
        paired = np.logical_and(pmsk[:, 0], pmsk[:, 1])
        if use_uid is None:
            assert not paired.any()
        elif paired.any():
            cmp_arrays(x1[paired, 0], x2[paired, 0])
    dataset.clean()


def test_configure_dataset(rna):
    with pytest.raises(ValueError):
        scglue.models.configure_dataset(rna, "NB", use_highly_variable=True)
    with pytest.raises(ValueError):
        scglue.models.configure_dataset(rna, "NB", use_highly_variable=False, use_layer="xxx")
    with pytest.raises(ValueError):
        scglue.models.configure_dataset(rna, "NB", use_highly_variable=False, use_rep="xxx")
    with pytest.raises(ValueError):
        scglue.models.configure_dataset(rna, "NB", use_highly_variable=False, use_dsc_weight="xxx")
    with pytest.raises(ValueError):
        scglue.models.configure_dataset(rna, "NB", use_highly_variable=False, use_uid="xxx")
    with pytest.raises(ValueError):
        scglue.models.configure_dataset(rna, "NB", use_highly_variable=False, use_cell_type="xxx")
    scglue.models.configure_dataset(rna, "NB", use_highly_variable=False)


@pytest.mark.parametrize("rna_prob", ["NB"])
@pytest.mark.parametrize("atac_prob", ["NB", "ZINB"])
@pytest.mark.parametrize("model", ["IndSCGLUE", "SCGLUE", "PairedSCGLUE"])
@pytest.mark.parametrize("backed", [False, True])
def test_save_load(rna_pp, atac_pp, prior, tmp_path, rna_prob, atac_prob, model, backed):

    if model == "SCGLUE":
        ActiveModel = scglue.models.SCGLUEModel
    elif model == "PairedSCGLUE":
        ActiveModel = scglue.models.PairedSCGLUEModel
    elif model == "IndSCGLUE":
        ActiveModel = scglue.models.IndSCGLUEModel
    else:
        raise ValueError("Invalid model!")

    if backed:
        rna_pp.write_h5ad(tmp_path / "rna_pp.h5ad")
        atac_pp.write_h5ad(tmp_path / "atac_pp.h5ad")
        rna_pp = anndata.read_h5ad(tmp_path / "rna_pp.h5ad", backed="r")
        atac_pp = anndata.read_h5ad(tmp_path / "atac_pp.h5ad", backed="r")

    scglue.models.configure_dataset(rna_pp, rna_prob, use_rep="X_pca", use_highly_variable=True, use_cell_type="ct", use_dsc_weight="dsc_weight")
    scglue.models.configure_dataset(atac_pp, atac_prob, use_rep="X_lsi", use_highly_variable=True, use_batch="batch", use_dsc_weight="dsc_weight")
    vertices = sorted(prior.nodes)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ValueError):
            glue = ActiveModel({}, vertices, latent_dim=2)
    glue = ActiveModel(
        {"rna": rna_pp, "atac": atac_pp}, vertices,
        latent_dim=2, random_seed=0
    )
    with pytest.raises(ValueError):
        glue.compile(lam_graph=None)
    glue.compile()
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp, "atac": atac_pp}, prior, max_epochs=None)
    glue.fit(
        {"rna": rna_pp, "atac": atac_pp}, prior,
        data_batch_size=32, graph_batch_size=128,
        align_burnin=2, max_epochs=100, patience=2,
        reduce_lr_patience=1,
        wait_n_lrs=3, directory=tmp_path
    )
    print(glue)
    with pytest.raises(RuntimeError):
        glue.net()

    rna_pp.obsm["X_glue1"] = glue.encode_data("rna", rna_pp)
    atac_pp.obsm["X_glue1"] = glue.encode_data("atac", atac_pp)
    graph_embedding1 = glue.encode_graph(prior)

    glue.get_losses({"rna": rna_pp, "atac": atac_pp}, prior)  # NOTE: Smoke test
    glue.decode_data("rna", "atac", rna_pp, prior)  # NOTE: Smoke test
    glue.decode_data(
        "rna", "atac", rna_pp, prior,
        target_batch=rna_pp.obs["batch"]
    )  # NOTE: Smoke test

    glue.save(tmp_path / "final.dill")
    glue_load = scglue.models.load_model(tmp_path / "final.dill")

    rna_pp.obsm["X_glue2"] = glue_load.encode_data("rna", rna_pp)
    atac_pp.obsm["X_glue2"] = glue_load.encode_data("atac", atac_pp)
    graph_embedding2 = glue_load.encode_graph(prior)

    cmp_arrays(rna_pp.obsm["X_glue1"], rna_pp.obsm["X_glue2"])
    cmp_arrays(atac_pp.obsm["X_glue1"], atac_pp.obsm["X_glue2"])
    cmp_arrays(graph_embedding1, graph_embedding2)

    if backed:
        with pytest.raises(RuntimeError):
            scglue.models.integration_consistency(
                glue, {"rna": rna_pp, "atac": atac_pp}, prior
            )
    else:
        glue.net.x2u["atac"] = glue.net.x2u["rna"]
        atac_pp.obsm["X_lsi"] = rna_pp.obsm["X_pca"][:atac_pp.n_obs]
        scglue.models.integration_consistency(
            glue, {"rna": rna_pp, "atac": atac_pp}, prior
        )  # NOTE: Smoke test

    del glue, glue_load


@pytest.mark.parametrize("rna_prob", ["Normal"])
@pytest.mark.parametrize("atac_prob", ["ZIN", "ZILN"])
@pytest.mark.parametrize("model", ["IndSCGLUE", "SCGLUE", "PairedSCGLUE"])
def test_adopt_freeze(rna_pp, atac_pp, prior, tmp_path, rna_prob, atac_prob, model):

    if model == "SCGLUE":
        ActiveModel = scglue.models.SCGLUEModel
    elif model == "PairedSCGLUE":
        ActiveModel = scglue.models.PairedSCGLUEModel
    elif model == "IndSCGLUE":
        ActiveModel = scglue.models.IndSCGLUEModel
    else:
        raise ValueError("Invalid model!")

    scglue.models.configure_dataset(rna_pp, rna_prob, use_highly_variable=True, use_layer="arange", use_batch="batch")
    scglue.models.configure_dataset(atac_pp, atac_prob, use_highly_variable=True, use_rep="X_lsi")
    vertices = sorted(prior.nodes)

    glue = ActiveModel(
        {"rna": rna_pp, "atac": atac_pp}, vertices,
        latent_dim=2, random_seed=0
    )
    glue.compile()
    glue.fit(
        {"rna": rna_pp, "atac": atac_pp}, prior,
        data_batch_size=32, graph_batch_size=128,
        align_burnin=2, max_epochs=5, patience=3,
        directory=tmp_path
    )

    rna_pp.obsm["X_glue1"] = glue.encode_data("rna", rna_pp)
    atac_pp.obsm["X_glue1"] = glue.encode_data("atac", atac_pp)

    glue_freeze = ActiveModel(
        {"rna": rna_pp, "atac": atac_pp}, vertices,
        latent_dim=2, random_seed=0
    )
    glue_freeze.adopt_pretrained_model(glue, submodule="x2u")
    glue_freeze.compile()
    glue_freeze.freeze_cells()
    glue_freeze.fit(
        {"rna": rna_pp, "atac": atac_pp}, prior,
        data_batch_size=32, graph_batch_size=128,
        align_burnin=2, max_epochs=5, patience=3,
        directory=tmp_path
    )
    glue_freeze.unfreeze_cells()
    print(glue_freeze)

    rna_pp.obsm["X_glue2"] = glue_freeze.encode_data("rna", rna_pp)
    atac_pp.obsm["X_glue2"] = glue_freeze.encode_data("atac", atac_pp)

    cmp_arrays(rna_pp.obsm["X_glue1"], rna_pp.obsm["X_glue2"])
    cmp_arrays(atac_pp.obsm["X_glue1"], atac_pp.obsm["X_glue2"])

    glue_freeze = ActiveModel(
        {"rna": rna_pp, "atac": atac_pp}, vertices,
        latent_dim=4, h_depth=3, random_seed=0
    )
    glue_freeze.adopt_pretrained_model(glue, submodule="x2u")
    del glue, glue_freeze


@pytest.mark.cpu_only
@pytest.mark.parametrize("rna_prob", ["NB"])
@pytest.mark.parametrize("atac_prob", ["NB"])
@pytest.mark.parametrize("model", ["IndSCGLUE", "SCGLUE", "PairedSCGLUE"])
def test_repeatability(rna_pp, atac_pp, prior, tmp_path, rna_prob, atac_prob, model):

    if model == "SCGLUE":
        ActiveModel = scglue.models.SCGLUEModel
    elif model == "PairedSCGLUE":
        ActiveModel = scglue.models.PairedSCGLUEModel
    elif model == "IndSCGLUE":
        ActiveModel = scglue.models.IndSCGLUEModel
    else:
        raise ValueError("Invalid model!")

    scglue.models.configure_dataset(rna_pp, rna_prob, use_highly_variable=True, use_rep="X_pca", use_batch="batch", use_uid="uid")
    scglue.models.configure_dataset(atac_pp, atac_prob, use_highly_variable=True, use_cell_type="ct", use_uid="uid")
    vertices = sorted(prior.nodes)
    graph_embedding = {}

    for i in range(2):
        glue = ActiveModel(
            {"rna": rna_pp, "atac": atac_pp}, vertices,
            latent_dim=2, random_seed=0
        )
        if model == "PairedSCGLUE":
            glue.compile(lam_joint_cross=1, lam_real_cross=1, lam_cos=0.1)
        else:
            glue.compile()
        glue.fit(
            {"rna": rna_pp, "atac": atac_pp}, prior,
            data_batch_size=32, graph_batch_size=128,
            align_burnin=2, max_epochs=5, patience=3,
            directory=tmp_path
        )

        rna_pp.obsm[f"X_glue{i + 1}"] = glue.encode_data("rna", rna_pp)
        atac_pp.obsm[f"X_glue{i + 1}"] = glue.encode_data("atac", atac_pp)
        graph_embedding[i + 1] = glue.encode_graph(prior)

        del glue

    cmp_arrays(rna_pp.obsm["X_glue1"], rna_pp.obsm["X_glue2"])
    cmp_arrays(atac_pp.obsm["X_glue1"], atac_pp.obsm["X_glue2"])
    cmp_arrays(graph_embedding[1], graph_embedding[2])


@pytest.mark.parametrize("model", ["IndSCGLUE", "SCGLUE", "PairedSCGLUE"])
def test_abnormal(rna_pp, atac_pp, prior, tmp_path, model):

    if model == "SCGLUE":
        ActiveModel = scglue.models.SCGLUEModel
    elif model == "PairedSCGLUE":
        ActiveModel = scglue.models.PairedSCGLUEModel
    elif model == "IndSCGLUE":
        ActiveModel = scglue.models.IndSCGLUEModel
    else:
        raise ValueError("Invalid model!")

    vertices = sorted(prior.nodes)
    with pytest.raises(ValueError):
        glue = ActiveModel(
            {"rna": rna_pp, "atac": atac_pp}, vertices,
            latent_dim=2, random_seed=0
        )
    scglue.models.configure_dataset(rna_pp, "NB", use_highly_variable=False, use_rep="X_pca", use_layer="arange", use_dsc_weight="dsc_weight")
    scglue.models.configure_dataset(atac_pp, "zzz", use_highly_variable=False, use_rep="X_lsi")
    with pytest.raises(ValueError):
        glue = ActiveModel(
            {"rna": rna_pp, "atac": atac_pp}, vertices,
            latent_dim=2, random_seed=0
        )
    scglue.models.configure_dataset(atac_pp, "NB", use_highly_variable=False, use_rep="X_lsi", use_batch="batch", use_cell_type="ct", use_uid="uid")
    glue = ActiveModel(
        {"rna": rna_pp, "atac": atac_pp}, vertices,
        latent_dim=100, random_seed=0
    )
    glue.compile()

    atac_pp_cp = atac_pp.copy()
    del atac_pp_cp.obsm["X_lsi"]
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp, "atac": atac_pp_cp}, prior, directory=tmp_path)

    atac_pp_cp = atac_pp.copy()
    atac_pp_cp.obsm["X_lsi"] = atac_pp_cp.obsm["X_lsi"][:, :-1]
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp, "atac": atac_pp_cp}, prior, directory=tmp_path)

    rna_pp_cp = rna_pp.copy()
    del rna_pp_cp.layers["arange"]
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp_cp, "atac": atac_pp}, prior, directory=tmp_path)

    rna_pp_cp = rna_pp.copy()
    del rna_pp_cp.obs["dsc_weight"]
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp_cp, "atac": atac_pp}, prior, directory=tmp_path)

    atac_pp_cp = atac_pp.copy()
    del atac_pp_cp.obs["batch"]
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp, "atac": atac_pp_cp}, prior, directory=tmp_path)

    atac_pp_cp = atac_pp.copy()
    del atac_pp_cp.obs["ct"]
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp, "atac": atac_pp_cp}, prior, directory=tmp_path)

    atac_pp_cp = atac_pp.copy()
    del atac_pp_cp.obs["uid"]
    with pytest.raises(ValueError):
        glue.fit({"rna": rna_pp, "atac": atac_pp_cp}, prior, directory=tmp_path)

    atac_pp_cp = atac_pp.copy()
    atac_pp_cp.X = atac_pp_cp.X.astype(np.float64)
    atac_pp_cp.write_h5ad(tmp_path / "atac_pp_cp.h5ad")
    atac_pp_cp = anndata.read_h5ad(tmp_path / "atac_pp_cp.h5ad", backed="r")
    with pytest.raises(RuntimeError):
        glue.fit({"rna": rna_pp, "atac": atac_pp_cp}, prior, directory=tmp_path)

    with pytest.raises(ValueError):
        glue.encode_data("rna", atac_pp)
    with pytest.raises(ValueError):
        glue.encode_data("atac", rna_pp)
    del glue


def test_fit_SCGLUE(rna_pp, atac_pp, prior):
    scglue.models.configure_dataset(rna_pp, "NB", use_highly_variable=True, use_rep="X_pca", use_batch="batch", use_uid="uid")
    scglue.models.configure_dataset(atac_pp, "NB", use_highly_variable=True, use_cell_type="ct", use_batch="batch", use_uid="uid")
    scglue.models.fit_SCGLUE(
        {"rna": rna_pp, "atac": atac_pp}, prior,
        init_kws={"latent_dim": 2, "shared_batches": True},
        compile_kws={"lr": 1e-5},
        fit_kws={"max_epochs": 5}
    )  # NOTE: Smoke test

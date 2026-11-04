from scripts.audit_t04_activation_gaps import assess_candidates


def source(provider='TFDA', included=True, energy=0, protein=0):
    return {'provider':provider,'included_in_calculation':included,
            'values':{'energy_kcal':energy,'protein_g':protein}}


def test_explicit_zero_is_available_but_unknown_is_not():
    assert assess_candidates([source()])['tfda_links_with_energy_and_protein']==1
    result=assess_candidates([source(protein=None)])
    assert result['tfda_links_with_energy_and_protein']==0
    assert result['required_fields_absent_from_all_included_tfda_sources']==['protein_g']


def test_excluded_and_usda_sources_do_not_become_tfda_candidates():
    result=assess_candidates([source(included=False),source(provider='USDA')])
    assert result['included_tfda_source_links']==0
    assert result['tfda_links_with_energy_and_protein']==0


def test_values_from_different_samples_are_not_combined():
    result=assess_candidates([source(energy=10,protein=None),source(energy=None,protein=1)])
    assert result['tfda_links_with_energy_and_protein']==0


def test_recipe_derived_only_source_is_identified_without_using_its_values():
    result=assess_candidates([source(provider='recipe_dataset')])
    assert result['tfda_links_with_energy_and_protein']==0
    assert '歷史食譜推導' in result['source_gap_reason']
